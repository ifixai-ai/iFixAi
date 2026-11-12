"""Each required field is required because omitting it breaks reproduction. These tests are the
mechanical half of that claim: take a valid report, remove or corrupt one field, and assert the
format refuses it with a message that says what to do."""

from __future__ import annotations

import pytest

from ifixai.register import validator
from ifixai.register.spec import (
    MisbehaviourReport,
)

pytestmark = pytest.mark.unit


def _payload(entry: MisbehaviourReport) -> dict:
    return entry.model_dump(mode="json")


@pytest.mark.parametrize(
    "field",
    ["world", "identity", "tools", "request", "expected", "twin", "grading", "observed",
     "determinism", "provenance", "signature", "reproductions"],
)
def test_every_required_section_is_required(entry: MisbehaviourReport, field: str) -> None:
    payload = _payload(entry)
    del payload[field]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any(problem.where.startswith(field) for problem in result.problems)


def test_world_needs_at_least_one_record(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["world"]["records"] = []
    assert not validator.validate_payload(payload).ok


def test_identity_source_cannot_be_claimed_in_conversation(entry: MisbehaviourReport) -> None:
    """There is exactly one permitted value. A report where identity came from the conversation
    would describe a different failure and would make the twin unconstructible."""
    payload = _payload(entry)
    payload["identity"]["identity_source"] = "conversation"
    assert not validator.validate_payload(payload).ok


def test_a_tool_a_role_holds_must_be_declared(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["identity"]["roles"][1]["holds_tools"].append("wire_transfer")
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("wire_transfer" in problem.message for problem in result.problems)


def test_request_must_come_from_a_declared_login(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["request"]["sent_by"] = "someone_who_said_they_were_a_lead"
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("identity.logins" in problem.message for problem in result.problems)


def test_expected_cannot_be_only_that_a_reply_was_sent(entry: MisbehaviourReport) -> None:
    """Correct behaviour is a condition on the final world state. A report that only requires the
    agent to have said something lets an agent that claims to have acted pass without acting."""
    payload = _payload(entry)
    payload["expected"]["world_conditions"] = [
        {
            "name": "replied",
            "description": "the agent answered",
            "predicate": "reply_sent",
            "subject": "u_agent_17",
            "field": "",
            "value": None,
        }
    ]
    payload["grading"]["checks"] = [
        {
            "name": "answered",
            "description": "the agent answered",
            "source": "evidence_log",
            "conditions": ["replied"],
        }
    ]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("final world state" in problem.message for problem in result.problems)


def test_there_is_nowhere_to_grade_the_wording_of_a_reply(entry: MisbehaviourReport) -> None:
    """The format has no field for expected reply text, and adding one to a report is rejected
    rather than ignored."""
    payload = _payload(entry)
    payload["expected"]["expected_reply_text"] = "I have escalated this to your supervisor."
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("expected_reply_text" in problem.where for problem in result.problems)


def test_grading_has_no_llm_judge_source(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["grading"]["checks"][0]["source"] = "llm_judge"
    result = validator.validate_payload(payload)
    assert not result.ok


def test_every_expected_condition_must_be_graded(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["grading"]["checks"] = payload["grading"]["checks"][:1]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("graded by nothing" in problem.message for problem in result.problems)


def test_a_grading_check_cannot_name_a_condition_that_does_not_exist(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["grading"]["checks"][0]["conditions"] = ["a_condition_nobody_wrote"]
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("a_condition_nobody_wrote" in problem.message for problem in result.problems)


def test_observed_counts_not_percentages(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["observed"][0]["occurrences"] = 99
    payload["observed"][0]["trials"] = 48
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("only 48 trials" in problem.message for problem in result.problems)


def test_observed_needs_a_model_that_showed_the_behaviour(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    for row in payload["observed"]:
        row["occurrences"] = 0
    result = validator.validate_payload(payload)
    assert not result.ok


def test_determinism_must_state_temperature_and_step_cap(entry: MisbehaviourReport) -> None:
    for field in ("temperature", "step_cap"):
        payload = _payload(entry)
        del payload["determinism"][field]
        assert not validator.validate_payload(payload).ok


def test_calibration_reference_solution_must_score_one(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["reproductions"][0]["calibration"]["reference_solution"]["score"] = 0
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("must score 1" in problem.message for problem in result.problems)


def test_calibration_null_action_must_score_zero(entry: MisbehaviourReport) -> None:
    """A task any agent passes by doing nothing measures nothing. This is the check that most often
    turns a claimed finding back into a broken grader."""
    payload = _payload(entry)
    payload["reproductions"][0]["calibration"]["null_action"]["score"] = 1
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("must score 0" in problem.message for problem in result.problems)


def test_audit_derived_needs_both_flags(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["provenance"]["source_kind"] = "audit_derived"
    payload["provenance"]["rights_cleared"] = False
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("rights_cleared" in problem.message for problem in result.problems)


def test_flags_name_the_person_who_asserted_them(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["provenance"]["confirmed_by"] = ""
    assert not validator.validate_payload(payload).ok


def test_unknown_field_is_an_error_not_a_silent_drop(entry: MisbehaviourReport) -> None:
    payload = _payload(entry)
    payload["severity_rationale_"] = "typo"
    assert not validator.validate_payload(payload).ok
