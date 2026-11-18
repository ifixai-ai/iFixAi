"""Which selected inspections can run against this agent, and why each of the rest cannot.

Decided once, before anything calls the agent, from the fixture and the provider, then
(with a judge) from the fixture's stated purpose. The reasons say what the fixture says,
never what we found: nothing is scanned. A not-applicable inspection is left out of the
results and every score.
"""

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ifixai.core.types import EvaluationPipelineConfig, Fixture, NotApplicable
from ifixai.evaluation.pipeline import CURRENT_INSPECTION, EvaluationPipeline
from ifixai.harness.registry import INSPECTION_REGISTRY, SPEC_BY_ID
from ifixai.inspections.probe_context import (
    derive_denied_destructive_cells,
    is_destructive_tool,
)
from ifixai.inspections.probe_pack import ProbePack, load_probe_pack, resolve_pack_path
from ifixai.judge.config import JudgeConfig
from ifixai.providers.base import ChatProvider
from ifixai.providers.governance_mixin import GovernanceMixin, answers_itself
from ifixai.providers.mock_governance import MockGovernanceProvider
from ifixai.shared.domain_scope import has_domain_restriction

# The ability each check needs (`Fixture.abilities`): probing an agent that cannot do the
# thing only earns a pass for being unable.
ABILITY_NEEDED: dict[str, str] = {
    "M03": "trains_models",
    "V08": "trains_models",
    "M07": "works_with_agents",
    "P32": "works_with_agents",
    "M12": "serves_consumers",
}
NO_ABILITY_REASONS: dict[str, str] = {
    "trains_models": "the fixture says the agent doesn't train or fine-tune models",
    "works_with_agents": "the fixture says the agent doesn't work with other agents",
    "serves_consumers": "the fixture says the agent doesn't serve consumers (members of the public)",
}


@dataclass(frozen=True)
class FieldNeeded:
    """What a check builds its probes from, with no fallback worth scoring, and the reason
    it is set aside when the fixture lacks it."""

    present: Callable[[Fixture], bool]
    reason: str


def has_destructive_tool(fixture: Fixture) -> bool:
    return any(is_destructive_tool(tool) for tool in fixture.tools)


def has_denied_destructive_cell(fixture: Fixture) -> bool:
    """Whether some user is denied a tool that can delete or overwrite: what P01's probes ask for.
    Without one they would fall back to a denied lookup tool, and a mandatory gate would grade an
    agent whose every role may delete on a request no role was refused. With no users the probes
    speak as an operator who holds no tool, so any such tool is denied to them."""
    if not fixture.users:
        return has_destructive_tool(fixture)
    return bool(derive_denied_destructive_cells(fixture))


# Each check's fields in order: the first one the fixture lacks gives the reason.
FIELD_NEEDED: dict[str, tuple[FieldNeeded, ...]] = {
    "P01": (
        FieldNeeded(has_destructive_tool, "the fixture lists no tool that can delete or overwrite"),
        FieldNeeded(
            has_denied_destructive_cell,
            "every role in the fixture may use each tool that can delete or overwrite, so no request "
            "for one crosses a role's limits",
        ),
    ),
    # Refusing off-topic requests would be wrong for a general-purpose agent.
    "B32": (
        FieldNeeded(
            has_domain_restriction,
            "the fixture names no specific domain, so a general-purpose agent has no topic to keep to",
        ),
    ),
}
# Checks whose chat questions can't score them alone (M06 has none; B11's one is under its
# floor): the agent's own hook or nothing.
HOOK_NEEDED: dict[str, str] = {"M06": "attest_response_identity", "B11": "invoke_tool"}
# B11's other hooks: in a run that wraps the agent with the fixture's `governance:`, one that
# block answers makes every hook item a note, leaving B11 under its floor.
FIXTURE_ANSWERED_HOOKS: dict[str, tuple[str, ...]] = {
    "B11": ("get_audit_trail", "apply_override", "get_configuration_version"),
}
NO_JUDGE_REASON: str = "needs a judge: its chat probes are graded by one, and this run has none"


def set_aside(
    test_ids: Iterable[str],
    fixture: Fixture,
    provider: ChatProvider,
    judge_config: JudgeConfig | None,
    pipeline_config: EvaluationPipelineConfig | None,
) -> list[NotApplicable]:
    """What a run sets aside before it starts, with why. Every runner entry point comes from
    here, and the CLI asks `find_not_applicable` with the class the run's agent will have
    (wrapped with the fixture's governance or not), so what the terminal lists is what the run
    skips."""
    has_judge = judge_config is not None and pipeline_config is not None
    return find_not_applicable(test_ids, fixture, type(provider), has_judge=has_judge)


def find_not_applicable(
    test_ids: Iterable[str], fixture: Fixture, provider_type: type[ChatProvider], *, has_judge: bool
) -> list[NotApplicable]:
    """The inspections in `test_ids` that cannot run against an agent of `provider_type`
    in a run with or without a judge, with why. The class, so a dry run needs no provider SDK;
    a class wrapped with governance (`governed_class`) says the run puts the fixture's
    `governance:` block on the agent."""
    found: list[NotApplicable] = []
    for test_id in test_ids:
        # What the fixture says the agent can't do comes first, as in `describe_blocking_reason`.
        reason = describe_fixture_reason(test_id, fixture)
        kind = "agent"
        if reason is None:
            reason = describe_blocking_reason(test_id, fixture, provider_type, has_judge=has_judge)
            kind = "run"
        if reason is not None:
            found.append(NotApplicable(test_id=test_id, name=SPEC_BY_ID[test_id].name, reason=reason, kind=kind))
    return found


def describe_blocking_reason(
    test_id: str, fixture: Fixture, provider_type: type[ChatProvider], *, has_judge: bool
) -> str | None:
    """Why `test_id` cannot run against this agent, or None when it can."""
    reason = describe_fixture_reason(test_id, fixture)
    if reason is not None:
        return reason
    hooks = (HOOK_NEEDED[test_id],) if test_id in HOOK_NEEDED else ()
    # Only a run that wraps the agent with the fixture's `governance:` has that block answer
    # hooks: never a bundled fixture's on any agent but the mock. Without one, the mock answers
    # them from its own built-in governance, as the agent.
    if fixture.governance is not None and issubclass(provider_type, GovernanceMixin):
        hooks += FIXTURE_ANSWERED_HOOKS.get(test_id, ())
    elif issubclass(provider_type, MockGovernanceProvider):
        hooks = ()
    missing = [f"`{h}`" for h in hooks if not answers_itself(provider_type, h)]
    if missing:
        return (
            f"needs the agent's own {', '.join(missing)} hook{'s' if len(missing) > 1 else ''} "
            "(a custom adapter), which chat can't stand in for; answers from the fixture's "
            "`governance:` block don't count"
        )
    if not has_judge and _only_a_judge_grades(test_id, fixture, provider_type):
        return NO_JUDGE_REASON
    return None


def _only_a_judge_grades(test_id: str, fixture: Fixture, provider_type: type[ChatProvider]) -> bool:
    """Whether only a judge can grade `test_id` here: a probe pack, or a check whose hook path
    (its `scoring_hook`) the agent may not answer or the fixture can't fill to the floor. A hook
    the provider only tries (`optional_hooks`) may never answer, and with no judge chat can't
    make up the rest."""
    if resolve_pack_path(test_id) is not None:
        return True
    inspection = INSPECTION_REGISTRY[test_id]
    hook = inspection.scoring_hook
    if hook is None:
        return False
    answers = answers_itself(provider_type, hook) and hook not in provider_type.optional_hooks
    return not answers or inspection.hook_items(fixture) < SPEC_BY_ID[test_id].min_evidence_items


def describe_fixture_reason(test_id: str, fixture: Fixture) -> str | None:
    """Why the fixture alone sets `test_id` aside, whatever the agent or judge, or None."""
    ability = ABILITY_NEEDED.get(test_id)
    if ability is not None and getattr(fixture.abilities, ability) is False:
        return NO_ABILITY_REASONS[ability]
    for needed in FIELD_NEEDED.get(test_id, ()):
        if not needed.present(fixture):
            return needed.reason
    return None


async def find_off_purpose(
    test_ids: Iterable[str], fixture: Fixture, pipeline: EvaluationPipeline
) -> list[NotApplicable]:
    """The checks whose work (their pack's `purpose_decides`) the fixture's stated purpose
    leaves out, asked of the judge once each before any probe is sent. Abilities the
    fixture declares are taken at its word and skip the question."""
    found: list[NotApplicable] = []
    for test_id in test_ids:
        pack = await asyncio.to_thread(load_probe_pack, test_id)
        if pack is None or not pack.purpose_decides:
            continue
        ability = ABILITY_NEEDED.get(test_id)
        if ability is not None and getattr(fixture.abilities, ability) is True:
            continue
        token = CURRENT_INSPECTION.set(test_id)
        try:
            in_purpose = await is_work_in_purpose(pipeline, pack, fixture)
        finally:
            CURRENT_INSPECTION.reset(token)
        if not in_purpose:
            reason = f"the fixture's stated purpose doesn't cover {pack.purpose_decides}"
            found.append(NotApplicable(test_id=test_id, name=SPEC_BY_ID[test_id].name, reason=reason))
    return found


async def is_work_in_purpose(pipeline: EvaluationPipeline, pack: ProbePack, fixture: Fixture) -> bool:
    """Whether the pack's `purpose_decides` work is the agent's job. With no stated purpose,
    or an off-format answer, the work counts as the agent's and runs; a judge that was down or
    out of budget leaves the inspection INCONCLUSIVE instead (see `probe_pack.execute_inspection`)."""
    purpose = (fixture.system_purpose or fixture.metadata.description).strip()
    if not purpose:
        return True
    answer = await pipeline.is_purpose_work(purpose, pack.purpose_decides)
    return answer if isinstance(answer, bool) else True
