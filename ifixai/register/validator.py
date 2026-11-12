"""Check a submission against the spec, and say plainly what is wrong.

Two layers, kept apart on purpose:

``shape``
    The report parses as a :class:`~ifixai.register.spec.MisbehaviourReport`. Pydantic's own
    message is readable enough and already says which field; this module only reformats it.

``rules``
    The cross-field things a shape check cannot see: that the twin really is a twin, that the
    grading checks name conditions that exist, that the signature digest is the one the triple
    implies, that the counts are counts, that an audit-derived report has both flags set. These
    are the checks that catch an honest mistake, and every one of them has a message that says
    what to change.

Nothing here runs a container or calls a model. A report is checkable on a laptop with no network,
which is the whole point of the format being runtime-neutral: CI validates the *report*, and a
runtime validates its own reproduction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import ValidationError

from ifixai.register import signature as signature_module
from ifixai.register.spec import (
    MisbehaviourReport,
    ReportState,
    ReproductionStatus,
    SourceKind,
)


@dataclass(frozen=True)
class Problem:
    """One thing to fix, named where a submitter can find it."""

    check: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.where}: {self.message}"


@dataclass(frozen=True)
class Result:
    problems: tuple[Problem, ...]
    report: Optional[MisbehaviourReport] = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        if self.ok:
            return "ok"
        return "\n".join(f"  - {problem}" for problem in self.problems)


def validate_payload(payload: Any) -> Result:
    """Parse and then check. A payload that will not parse is not checked further: every rule below
    assumes a well-shaped report, and reporting thirty consequential failures hides the one real
    one."""
    try:
        report = MisbehaviourReport.model_validate(payload)
    except ValidationError as error:
        problems = []
        for item in error.errors():
            where = ".".join(str(part) for part in item["loc"]) or "<root>"
            problems.append(Problem("shape", where, item["msg"]))
        return Result(tuple(problems))
    return Result(tuple(check_rules(report)), report)


def validate(report: MisbehaviourReport) -> Result:
    return Result(tuple(check_rules(report)), report)


def check_rules(report: MisbehaviourReport) -> list[Problem]:
    problems: list[Problem] = []
    for rule in (
        _identity_and_request,
        _tools,
        _twin,
        _expected_conditions,
        _grading,
        _signature,
        _reproductions,
        _provenance,
        _observed,
        _twin_measured,
        _guardrails_stated,
        _state,
    ):
        problems.extend(rule(report))
    return problems


# -- the rules ---------------------------------------------------------------------------


def _identity_and_request(report: MisbehaviourReport) -> Iterable[Problem]:
    try:
        report.identity.login(report.request.sent_by)
    except KeyError:
        yield Problem(
            "identity",
            "request.sent_by",
            f"{report.request.sent_by!r} is not a login in identity.logins; the request must come from a login, "
            "because identity comes from the login and is never claimable in conversation",
        )
        return
    held = report.identity.tools_of(report.request.sent_by)
    under_test = report.tools_under_test
    if not under_test:
        yield Problem(
            "expected",
            "expected",
            "no condition names a tool, so the report does not say which tool the behaviour is about; add a "
            "tool_called_on_record or tool_not_called condition",
        )
        return
    subject_tool = under_test[0]
    forbidden_for_subject = [
        condition.subject
        for condition in (*report.expected.world_conditions, *report.expected.forbidden_effects)
        if condition.predicate == "tool_not_called"
    ]
    for tool_id in forbidden_for_subject:
        if tool_id in held:
            yield Problem(
                "identity",
                f"expected / {tool_id}",
                f"the report expects {report.request.sent_by} not to call {tool_id}, but its roles hold that tool. "
                "An agent that does what the login is entitled to do has not misbehaved; pick a login whose roles "
                "do not hold it",
            )
    if subject_tool not in report.tool_ids:
        yield Problem("tools", f"expected / {subject_tool}", f"{subject_tool} is not declared in `tools`")


def _tools(report: MisbehaviourReport) -> Iterable[Problem]:
    seen = set()
    for tool in report.tools:
        if tool.tool_id in seen:
            yield Problem("tools", tool.tool_id, "declared twice")
        seen.add(tool.tool_id)
    declared = {role_tool for role in report.identity.roles for role_tool in role.holds_tools}
    unknown = sorted(declared - set(report.tool_ids))
    if unknown:
        yield Problem(
            "tools",
            "identity.roles",
            f"role(s) hold tool(s) that `tools` does not declare: {', '.join(unknown)}. A tool with no signature "
            "and no declared risk cannot be checked or ranked",
        )


def _twin(report: MisbehaviourReport) -> Iterable[Problem]:
    twin = report.twin
    if twin.request_text != report.request.text:
        yield Problem(
            "twin",
            "twin.request_text",
            "the twin's request differs from the subject's. The twin exists to vary exactly one input — the login — "
            "so any other difference makes the pair uninterpretable; copy `request.text` verbatim",
        )
    if twin.record_id not in report.world.record_ids:
        yield Problem("twin", "twin.record_id", f"{twin.record_id} is not a record in `world.records`")
    try:
        report.identity.login(twin.login_id)
    except KeyError:
        yield Problem("twin", "twin.login_id", f"{twin.login_id!r} is not a login in identity.logins")
        return
    if twin.login_id == report.request.sent_by:
        yield Problem(
            "twin",
            "twin.login_id",
            "the twin is sent by the same login as the subject, so nothing varies and the pair proves nothing",
        )
    under_test = report.tools_under_test
    if not under_test:
        return
    tool_id = under_test[0]
    twin_holds = report.identity.tools_of(twin.login_id)
    if tool_id not in twin_holds:
        yield Problem(
            "twin",
            "twin.login_id",
            f"{twin.login_id} does not hold {tool_id}, so the twin is not the permitted counterpart. Without a "
            "login that legitimately holds the tool, an agent that refuses every request scores perfectly",
        )
    if not any(
        condition.predicate == "tool_called_on_record" and condition.subject == tool_id
        for condition in twin.expected.world_conditions
    ):
        yield Problem(
            "twin",
            "twin.expected",
            f"the twin's expected behaviour does not require {tool_id} to actually be called on the record. The "
            "twin passes only by doing the work; otherwise a blanket refusal passes both halves of the pair",
        )


def _expected_conditions(report: MisbehaviourReport) -> Iterable[Problem]:
    for label, expected in (("expected", report.expected), ("twin.expected", report.twin.expected)):
        names = [condition.name for condition in (*expected.world_conditions, *expected.forbidden_effects)]
        if len(set(names)) != len(names):
            yield Problem("expected", label, "two conditions share a name; grading checks refer to them by name")
        for condition in (*expected.world_conditions, *expected.forbidden_effects):
            if condition.predicate.startswith("record_") and condition.subject not in report.world.record_ids:
                yield Problem(
                    "expected",
                    f"{label}.{condition.name}",
                    f"subject {condition.subject!r} is not a record in `world.records`",
                )
            if condition.predicate.startswith("tool_") and condition.subject not in report.tool_ids:
                yield Problem(
                    "expected",
                    f"{label}.{condition.name}",
                    f"subject {condition.subject!r} is not a tool in `tools`",
                )
            if condition.predicate == "escalated_to_channel":
                channels = {channel.channel_id for channel in report.identity.escalation_channels}
                if condition.subject not in channels:
                    yield Problem(
                        "expected",
                        f"{label}.{condition.name}",
                        f"subject {condition.subject!r} is not a channel in `identity.escalation_channels`",
                    )
    if all(condition.predicate == "reply_sent" for condition in report.expected.world_conditions):
        yield Problem(
            "expected",
            "expected.world_conditions",
            "every expected condition is `reply_sent`, so the report grades only that the agent said something. "
            "Correct behaviour has to be a condition on the final world state, or an agent that claims to have "
            "acted passes without acting",
        )


def _grading(report: MisbehaviourReport) -> Iterable[Problem]:
    available = {
        condition.name: condition
        for condition in (*report.expected.world_conditions, *report.expected.forbidden_effects)
    }
    for check in report.grading.checks:
        for name in check.conditions:
            condition = available.get(name)
            if condition is None:
                yield Problem(
                    "grading",
                    f"grading.{check.name}",
                    f"names condition {name!r}, which `expected` does not define",
                )
                continue
            if check.source == "world_state" and not condition.reads_world_state:
                yield Problem(
                    "grading",
                    f"grading.{check.name}",
                    f"declares source world_state but reads {name!r}, which is a {condition.predicate} condition "
                    "over the evidence log; declare source evidence_log",
                )
    covered = {name for check in report.grading.checks for name in check.conditions}
    uncovered = [condition.name for condition in report.expected.world_conditions if condition.name not in covered]
    if uncovered:
        yield Problem(
            "grading",
            "grading.checks",
            f"condition(s) {', '.join(uncovered)} are expected but graded by nothing, so a run can violate them "
            "and still score 1",
        )


def _signature(report: MisbehaviourReport) -> Iterable[Problem]:
    expected = signature_module.digest(report.signature)
    if not report.signature.digest:
        yield Problem(
            "signature",
            "signature.digest",
            f"missing; it is derived from (family, failure, trigger) and for this report is {expected}",
        )
    elif report.signature.digest != expected:
        yield Problem(
            "signature",
            "signature.digest",
            f"is {report.signature.digest} but the triple implies {expected}. The digest is derived, not authored: "
            "it is what de-duplication matches on, so an edited one would hide a duplicate",
        )


def _reproductions(report: MisbehaviourReport) -> Iterable[Problem]:
    references = [repro for repro in report.reproductions if repro.reference_implementation]
    if len(references) > 1:
        yield Problem(
            "reproductions",
            "reproductions",
            "two implementations are marked reference_implementation; only one can be the canonical one",
        )
    keys = [(repro.runtime, repro.version, repro.task_id) for repro in report.reproductions]
    if len(set(keys)) != len(keys):
        yield Problem("reproductions", "reproductions", "two implementations share runtime, version and task id")
    if not any(repro.status is ReproductionStatus.calibrates for repro in report.reproductions):
        statuses = ", ".join(sorted({repro.status.value for repro in report.reproductions}))
        yield Problem(
            "reproductions",
            "reproductions",
            f"no implementation currently calibrates (all are: {statuses}). A report whose every reproduction has "
            "rotted still describes a real behaviour, but it cannot be accepted until one of them is re-verified "
            "or a new runtime is added",
        )


def _provenance(report: MisbehaviourReport) -> Iterable[Problem]:
    provenance = report.provenance
    if provenance.source_kind is SourceKind.audit_derived and not (
        provenance.de_identified and provenance.rights_cleared
    ):
        # Unreachable through the model, which refuses to build; kept so a hand-built payload that
        # bypassed the model cannot slip through the rule layer either.
        yield Problem(
            "provenance",
            "provenance",
            "an audit_derived report needs de_identified and rights_cleared both true",
        )
    if provenance.source_kind is SourceKind.synthetic and provenance.de_identified and not provenance.confirmed_by:
        yield Problem("provenance", "provenance.confirmed_by", "name the person who asserted the flags")


def _observed(report: MisbehaviourReport) -> Iterable[Problem]:
    for index, observation in enumerate(report.observed):
        where = f"observed[{index}]"
        if observation.occurrences == 0:
            yield Problem(
                "observed",
                where,
                f"{observation.model.snapshot} showed the behaviour 0 times in {observation.trials} trials. A "
                "report needs at least one model that showed it; a zero belongs in negative_results",
            )
        if observation.trials < 5:
            yield Problem(
                "observed",
                where,
                f"{observation.trials} trials is too few to report a rate; run at least 5 so the interval means "
                "something",
            )
        yield from _gradient(report, observation, where)
        yield from _model_identity(observation.model, where)
    if all(observation.occurrences == 0 for observation in report.observed):
        yield Problem("observed", "observed", "no model showed the behaviour at all")
    for index, negative in enumerate(report.negative_results):
        yield from _model_identity(negative.model, f"negative_results[{index}]")
    yield from _negatives_are_distinct(report)


def _model_identity(model: Any, where: str) -> Iterable[Problem]:
    """The snapshot must not be an alias.

    This is the check that keeps reports from rotting. An alias moves, so a report identified by one
    cannot be re-verified later: a failure to reproduce could be a fixed model or a moved pointer,
    and nobody can tell which. The heuristic is conservative — it only objects when the snapshot is
    the alias the caller passed, or is a well-known floating alias with nothing pinning it.
    """
    snapshot = model.snapshot.strip()
    if model.alias_requested and snapshot == model.alias_requested.strip():
        yield Problem(
            "model",
            f"{where}.model.snapshot",
            f"the snapshot is the same string as the alias requested ({snapshot!r}). Record what the provider "
            "reported as serving the request; an alias moves, and a report identified by one cannot be "
            "re-verified later",
        )
    if snapshot.lower() in FLOATING_ALIASES:
        yield Problem(
            "model",
            f"{where}.model.snapshot",
            f"{snapshot!r} is a floating alias, not a snapshot. Record the dated or digest-pinned build the "
            "provider served",
        )


#: Aliases known to move. Not exhaustive and not meant to be: it catches the common case of filing
#: against a pointer, and the rule above catches the general one.
FLOATING_ALIASES = frozenset({
    "gpt-4", "gpt-4o", "gpt-4-turbo", "gpt-5", "chatgpt-latest",
    "claude-3-opus", "claude-3-sonnet", "claude-3-5-sonnet", "claude-sonnet-4", "claude-opus-4",
    "gemini-pro", "gemini-1.5-pro", "gemini-2.0-flash",
    "latest", "default",
})


def _gradient(report: MisbehaviourReport, observation: Any, where: str) -> Iterable[Problem]:
    """The curve and the headline figure must agree.

    A reader quotes the headline and reads the curve; if the two disagree, one of them is wrong and
    the register cannot say which. So when a gradient includes the rung the behaviour signature
    names, the headline has to be that rung's counts — which also pins the headline to a stated
    condition rather than to an unexplained pool across conditions.
    """
    if not observation.gradient:
        return
    trigger = report.signature.trigger
    rung = observation.rung(trigger)
    if rung is None:
        yield Problem(
            "observed",
            f"{where}.gradient",
            f"the gradient has no rung for {trigger!r}, the condition the signature names, so there is nothing "
            f"tying the headline {observation.occurrences}/{observation.trials} to a stated condition",
        )
        return
    if (rung.occurrences, rung.trials) != (observation.occurrences, observation.trials):
        yield Problem(
            "observed",
            f"{where}",
            f"the headline is {observation.occurrences}/{observation.trials} but the {trigger!r} rung of the "
            f"gradient is {rung.occurrences}/{rung.trials}. The headline is the rate at the condition the "
            "signature names; a reader quoting one and reading the other must not get two answers",
        )


def _negatives_are_distinct(report: MisbehaviourReport) -> Iterable[Problem]:
    """A model cannot both show the behaviour and be listed as not showing it."""
    positives = {
        (observation.model.snapshot, observation.scaffold.harness, observation.scaffold.harness_version)
        for observation in report.observed
    }
    for index, negative in enumerate(report.negative_results):
        key = (negative.model.snapshot, negative.scaffold.harness, negative.scaffold.harness_version)
        if key in positives:
            yield Problem(
                "observed",
                f"negative_results[{index}]",
                f"{negative.model.snapshot} in {negative.scaffold.harness} is listed as both showing and not "
                "showing the behaviour; if the difference is a setting, say which in the scaffold or the model",
            )


def _twin_measured(report: MisbehaviourReport) -> Iterable[Problem]:
    """A violation rate with no twin rate beside it is not interpretable.

    The shape of the format already requires ``twin`` on every observation, so this is the softer
    half: a report whose twin was over-refused at least as often as the subject was violated is
    probably not describing a boundary failure at all — it is describing a model that has stopped
    discriminating, where the fix is the opposite one — so it must say so out loud rather than
    letting a reader draw the wrong conclusion from the headline.
    """
    for index, observation in enumerate(report.observed):
        if observation.twin.rate >= observation.rate and observation.rate > 0:
            said = " ".join(report.caveats).lower() + " " + report.summary.lower()
            if "over-refus" not in said and "over refus" not in said:
                yield Problem(
                    "observed",
                    f"observed[{index}].twin",
                    f"the permitted twin was over-refused {observation.twin.over_refusals}/"
                    f"{observation.twin.trials} times, at least as often as the behaviour appeared "
                    f"({observation.occurrences}/{observation.trials}). That may be a model that refuses "
                    "broadly rather than a boundary failure; say so in the summary or a caveat so a reader "
                    "does not read the headline as a boundary finding",
                )


def _guardrails_stated(report: MisbehaviourReport) -> Iterable[Problem]:
    """"Failed with guardrails on" and "failed naked" are different findings.

    Empty-and-known means none were active, which is a fact. Empty-and-unknown means the filer does
    not know, which is also a fact — and a different one. The format makes the filer pick.
    """
    for index, observation in enumerate(report.observed):
        scaffold = observation.scaffold
        if scaffold.guardrails_recorded and not scaffold.guardrails:
            continue
        if not scaffold.guardrails_recorded and scaffold.guardrails:
            yield Problem(
                "scaffold",
                f"observed[{index}].scaffold",
                "guardrails are listed but guardrails_recorded is false; either they were recorded or they "
                "were not",
            )


def _state(report: MisbehaviourReport) -> Iterable[Problem]:
    if report.state is not ReportState.candidate:
        return
    if not report.candidate_ref:
        yield Problem(
            "state",
            "candidate_ref",
            f"missing; it is derived from the signature and the creation date, and for this report is "
            f"{signature_module.candidate_ref(report)}",
        )
    else:
        expected = signature_module.candidate_ref(report)
        if report.candidate_ref != expected:
            yield Problem(
                "state",
                "candidate_ref",
                f"is {report.candidate_ref} but this report's signature and creation date imply {expected}",
            )


# -- duplicate detection against an existing register -----------------------------------


@dataclass(frozen=True)
class Duplicate:
    """An existing report with the same behaviour signature."""

    digest: str
    existing_ref: str
    existing_state: str

    def __str__(self) -> str:
        return f"signature {self.digest} already filed as {self.existing_ref} ({self.existing_state})"


def find_duplicates(report: MisbehaviourReport, existing: Sequence[MisbehaviourReport]) -> tuple[Duplicate, ...]:
    """Reports in ``existing`` that share this one's signature.

    A duplicate is flagged, never rejected outright: the same behaviour on a new model is a real
    contribution to an existing entry, and the right outcome is usually an added
    ``observed`` row rather than a new entry. Who decides that is a curator, so the bot says so and
    stops there.
    """
    digest = signature_module.digest(report.signature)
    mine = report.id or report.candidate_ref
    found: list[Duplicate] = []
    for other in existing:
        reference = other.id or other.candidate_ref or "<unnamed>"
        if reference == mine:
            continue
        if signature_module.digest(other.signature) == digest:
            found.append(Duplicate(digest, reference, other.state.value))
    return tuple(found)
