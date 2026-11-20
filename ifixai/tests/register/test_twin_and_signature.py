"""The twin and the behaviour signature.

The twin is the field published misbehaviour results most often lack, and the one that decides
whether a report measures a safety property or a capability loss. The signature is what stops the
register filling with the same finding under forty names."""

from __future__ import annotations

import pytest

from ifixai.register import signature, validator
from ifixai.register.spec import BehaviourSignature, MisbehaviourReport

pytestmark = pytest.mark.unit


def _payload(entry: MisbehaviourReport) -> dict:
    return entry.model_dump(mode="json")


def test_twin_request_must_be_identical(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["twin"]["request_text"] = entry.request.text + " Thanks!"
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("exactly one input" in problem.message for problem in result.problems)


def test_twin_login_must_actually_hold_the_tool(entry: MisbehaviourReport) -> None:
    """Without a login that legitimately holds the tool, an agent that refuses everything scores
    perfectly — which is the whole reason the twin is required."""
    payload = _payload(entry)
    payload["twin"]["login_id"] = "u_agent_17"
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("refuses every request scores perfectly" in problem.message for problem in result.problems)


def test_twin_cannot_be_the_same_login_as_the_subject(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["twin"]["login_id"] = payload["request"]["sent_by"]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("nothing varies" in problem.message for problem in result.problems)


def test_twin_must_pass_only_by_doing_the_work(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["twin"]["expected"]["world_conditions"] = [
        condition
        for condition in payload["twin"]["expected"]["world_conditions"]
        if condition["predicate"] != "tool_called_on_record"
    ]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("blanket refusal" in problem.message for problem in result.problems)


def test_twin_record_must_be_the_record_under_test(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["twin"]["record_id"] = "REC-9999"
    assert not validator.validate_payload(payload).ok


def test_subject_login_must_not_hold_the_tool_it_is_expected_not_to_call(entry: MisbehaviourReport) -> None:
    """An agent that does what its login is entitled to do has not misbehaved."""
    payload = _payload(entry)
    payload["request"]["sent_by"] = "u_lead_04"
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("has not misbehaved" in problem.message for problem in result.problems)


# -- signature ---------------------------------------------------------------------------


def test_signature_normalises_case_and_separators() -> None:
    left = BehaviourSignature(family="Authority Boundary", failure="Authority.Violation", trigger="Injected-Note")
    right = BehaviourSignature(family="authority_boundary", failure="authority_violation", trigger="injected_note")
    assert signature.digest(left) == signature.digest(right)


def test_signature_distinguishes_different_triggers() -> None:
    left = BehaviourSignature(family="authority_boundary", failure="authority.violation", trigger="injected_note")
    right = BehaviourSignature(family="authority_boundary", failure="authority.violation", trigger="pressure_urgency")
    assert signature.digest(left) != signature.digest(right)


def test_a_hand_edited_digest_is_rejected(entry: MisbehaviourReport) -> None:
    """The digest is what de-duplication matches on, so an edited one would hide a duplicate."""
    payload = _payload(entry)
    payload["signature"]["digest"] = "0" * 12
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("derived, not authored" in problem.message for problem in result.problems)


def test_duplicates_are_found_across_states(entry: MisbehaviourReport, candidate: MisbehaviourReport) -> None:
    same = candidate.model_copy(update={"signature": signature.with_digest(entry.signature)})
    found = validator.find_duplicates(same, [entry])
    assert found and found[0].existing_ref == entry.id
    assert "already filed" in str(found[0])


def test_a_report_is_not_its_own_duplicate(entry: MisbehaviourReport) -> None:
    assert validator.find_duplicates(entry, [entry]) == ()


def test_different_behaviours_are_not_duplicates(
    entry: MisbehaviourReport, candidate: MisbehaviourReport
) -> None:
    assert validator.find_duplicates(candidate, [entry]) == ()


def test_candidate_ref_is_derived_from_the_signature(candidate: MisbehaviourReport) -> None:
    assert candidate.candidate_ref == signature.candidate_ref(candidate)
    payload = candidate.model_dump(mode="json")
    payload["candidate_ref"] = "CAND-000000000000-20260101"
    result = validator.validate_payload(payload)
    assert not result.ok


def test_a_candidate_cannot_claim_a_permanent_id(candidate: MisbehaviourReport) -> None:
    payload = candidate.model_dump(mode="json")
    payload["id"] = "AGENT-2026-9999"
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("never claimed in a submission" in problem.message for problem in result.problems)
