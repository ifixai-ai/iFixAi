"""The capture set beyond the eleven required fields.

Each of these exists because a finding that cannot be told apart from a neighbouring finding gets
argued about instead of fixed. The tests are the mechanical half of that: take a valid report,
record the thing imprecisely, and assert the validator says which field and why.
"""

from __future__ import annotations

import pytest

from ifixai.register import validator
from ifixai.register.spec import MisbehaviourReport, ModelIdentity, Route

pytestmark = pytest.mark.unit


def _payload(report: MisbehaviourReport) -> dict:
    return report.model_dump(mode="json")


# -- model identity ----------------------------------------------------------------------


def test_the_snapshot_cannot_be_the_alias(entry: MisbehaviourReport) -> None:
    """An alias moves. A report identified by one cannot be re-verified later, because a failure to
    reproduce could be a fixed model or a moved pointer and nobody can tell which."""
    payload = _payload(entry)
    payload["observed"][0]["model"]["snapshot"] = payload["observed"][0]["model"]["alias_requested"]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("an alias moves" in problem.message for problem in result.problems)


@pytest.mark.parametrize("alias", ["gpt-4o", "claude-sonnet-4", "gemini-1.5-pro", "latest"])
def test_a_known_floating_alias_is_rejected_as_a_snapshot(entry: MisbehaviourReport, alias: str) -> None:
    payload = _payload(entry)
    payload["observed"][0]["model"]["snapshot"] = alias
    payload["observed"][0]["model"]["alias_requested"] = ""
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("floating alias" in problem.message for problem in result.problems)


def test_a_gateway_route_must_name_the_gateway() -> None:
    """The same snapshot behind a gateway is a different system, and almost nobody records which."""
    with pytest.raises(ValueError, match="no gateway is named"):
        ModelIdentity(snapshot="x-2026-01-01", provider="p", route=Route.gateway)


def test_a_direct_route_cannot_also_name_a_gateway() -> None:
    with pytest.raises(ValueError, match="says direct"):
        ModelIdentity(snapshot="x-2026-01-01", provider="p", route=Route.direct, gateway="portkey")


def test_the_prompt_and_tool_hashes_are_hashes_not_contents() -> None:
    """Two reports are not comparable without knowing whether they shared a system prompt, and
    almost nobody can publish one. A hash settles it without disclosing anything — so the field
    refuses anything that is not a hash, including a system prompt pasted into it."""
    with pytest.raises(ValueError, match="never the contents"):
        ModelIdentity(
            snapshot="x-2026-01-01",
            provider="p",
            system_prompt_sha256="You are a helpful refunds assistant. Never refund over $500.",
        )


def test_the_worked_example_records_a_gateway_run(entry: MisbehaviourReport) -> None:
    routes = {observation.model.route for observation in entry.observed}
    assert Route.gateway in routes


# -- scaffold and guardrails -------------------------------------------------------------


def test_guardrails_on_and_guardrails_naked_are_distinguishable(entry: MisbehaviourReport) -> None:
    """"Failed with guardrails on" is a materially different finding from "failed naked", and the
    worked entry carries both so a reader can see the difference."""
    with_rails = [o for o in entry.observed if o.scaffold.guardrails]
    without = [o for o in entry.observed if not o.scaffold.guardrails]
    assert with_rails and without
    assert with_rails[0].rate < without[0].rate
    assert with_rails[0].scaffold.guardrails[0].mode == "blocking"


def test_unknown_guardrails_are_not_the_same_as_none(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["observed"][0]["scaffold"]["guardrails_recorded"] = False
    payload["observed"][0]["scaffold"]["guardrails"] = [{"name": "something", "version": "", "mode": "blocking"}]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("either they were recorded or they were not" in problem.message for problem in result.problems)


# -- the gradient ------------------------------------------------------------------------


def test_the_worked_entry_carries_a_dose_response_curve(entry: MisbehaviourReport) -> None:
    gradient = entry.observed[0].gradient
    variants = [rung.variant for rung in gradient]
    assert "base" in variants and "hard" in variants
    base = entry.observed[0].rung("base")
    hard = entry.observed[0].rung("hard")
    assert base is not None and hard is not None
    assert base.rate < hard.rate, "a curve that does not rise is not a dose-response curve"


def test_the_headline_must_match_the_rung_the_signature_names(entry: MisbehaviourReport) -> None:
    """A reader quotes the headline and reads the curve. If they disagree, one is wrong and the
    register cannot say which."""
    payload = _payload(entry)
    payload["observed"][0]["occurrences"] = 12
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("must not get two answers" in problem.message for problem in result.problems)


def test_a_gradient_without_the_signature_rung_is_rejected(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["observed"][0]["gradient"] = [
        rung for rung in payload["observed"][0]["gradient"] if rung["variant"] != entry.signature.trigger
    ]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("tying the headline" in problem.message for problem in result.problems)


def test_a_variant_cannot_appear_twice_in_one_gradient(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["observed"][0]["gradient"].append(payload["observed"][0]["gradient"][0])
    assert not validator.validate_payload(payload).ok


# -- the twin, measured ------------------------------------------------------------------


def test_every_observation_carries_the_twin_result(entry: MisbehaviourReport) -> None:
    """A violation rate without the twin's over-refusal rate beside it is not interpretable, so the
    format requires it on every observation rather than in a section of its own."""
    for observation in entry.observed:
        assert observation.twin.trials > 0
    payload = _payload(entry)
    del payload["observed"][0]["twin"]
    assert not validator.validate_payload(payload).ok


def test_an_over_refusing_model_must_be_called_out(entry: MisbehaviourReport) -> None:
    """37/48 violations with the twin served is a boundary failure. 37/48 with the twin over-refused
    40/48 times is a model that has stopped discriminating, and the fix is the opposite one — so the
    report has to say so rather than letting a reader take the headline at face value."""
    payload = _payload(entry)
    payload["observed"][0]["twin"]["over_refusals"] = 44
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("refuses broadly" in problem.message for problem in result.problems)
    # Saying so in a caveat is enough: the report is then interpretable.
    payload["caveats"] = [*payload["caveats"], "This model also over-refuses the permitted twin."]
    assert validator.validate_payload(payload).ok


# -- negative results --------------------------------------------------------------------


def test_the_worked_entry_names_a_model_that_did_not_do_it(entry: MisbehaviourReport) -> None:
    """The field most registers omit, and the one that makes a positive result mean something."""
    assert entry.negative_results
    negative = entry.negative_results[0]
    assert negative.trials >= 5
    assert negative.twin is not None, "a model that refuses everything would also show 0 here"


def test_a_negative_result_needs_enough_trials(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["negative_results"][0]["trials"] = 3
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("not enough to say a model does not do this" in problem.message for problem in result.problems)


def test_a_model_cannot_both_show_and_not_show_the_behaviour(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["negative_results"][0]["model"] = payload["observed"][0]["model"]
    payload["negative_results"][0]["scaffold"] = payload["observed"][0]["scaffold"]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("both showing and not showing" in problem.message for problem in result.problems)


# -- failure anatomy and determinism -----------------------------------------------------


def test_the_anatomy_records_the_deception_signals(entry: MisbehaviourReport, candidate: MisbehaviourReport) -> None:
    """An agent that states the boundary and crosses it anyway, or reports success for work it did
    not do, is not making a mistake about the rules. A rate alone cannot show that."""
    assert entry.observed[0].anatomy is not None
    assert entry.observed[0].anatomy.acknowledged_boundary_then_crossed is True
    assert candidate.observed[0].anatomy is not None
    assert candidate.observed[0].anatomy.reported_success_falsely is True
    assert candidate.observed[0].anatomy.first_violation_step == 2


def test_determinism_states_whether_it_reproduces_at_temperature_zero(entry: MisbehaviourReport) -> None:
    assert entry.determinism.reproduces_at_temperature_zero in ("yes", "no", "untested")
    assert entry.determinism.pass_at_k
    payload = _payload(entry)
    payload["determinism"]["reproduces_at_temperature_zero"] = "sometimes"
    assert not validator.validate_payload(payload).ok
