"""The format is runtime-neutral, and that is a property worth testing rather than asserting.

Three claims:

* a report is complete, valid and checkable with no runtime installed and no network;
* no runtime detail leaks into the fields that describe the behaviour — a reader can reconstruct
  the task from the report alone, in a runtime of their own;
* reproductions are plural and independently stateful, so Harbor is one implementation, a second
  runtime can be added without touching the report's identity, and a rotted binding is a state
  rather than a deletion.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from ifixai.register import signature, validator
from ifixai.register.spec import (
    Calibration,
    CalibrationRun,
    MisbehaviourReport,
    ReproductionImplementation,
    ReproductionStatus,
)

pytestmark = pytest.mark.unit

#: Words that would mean a runtime had leaked into the behaviour description.
RUNTIME_WORDS = (
    "harbor",
    "docker",
    "dockerfile",
    "container",
    "image",
    "sha256",
    "task.toml",
    "instruction.md",
    "solve.sh",
    "test.sh",
    "pytest",
    "ifixai run",
)


def _behaviour_only(report: MisbehaviourReport) -> str:
    """Everything that describes the behaviour, with the runtime bindings removed."""
    payload = report.model_dump(mode="json")
    payload.pop("reproductions")
    payload.pop("references", None)
    payload.pop("caveats", None)
    return json.dumps(payload).lower()


def test_no_runtime_detail_in_the_behaviour_description(
    entry: MisbehaviourReport, candidate: MisbehaviourReport
) -> None:
    for report in (entry, candidate):
        described = _behaviour_only(report)
        leaked = [word for word in RUNTIME_WORDS if word in described]
        assert leaked == [], f"{report.id or report.candidate_ref} names a runtime outside reproductions: {leaked}"


def test_a_report_validates_with_no_runtime_and_no_network(entry: MisbehaviourReport) -> None:
    """Validation reads the file and nothing else. This is what lets CI check a submission, and a
    stranger check the register, without installing anybody's harness."""
    assert validator.validate(entry).ok


def test_reproductions_are_plural(candidate: MisbehaviourReport) -> None:
    assert len(candidate.reproductions) > 1
    runtimes = {(repro.runtime, repro.version) for repro in candidate.reproductions}
    assert len(runtimes) == len(candidate.reproductions)


def test_harbor_is_the_reference_implementation_not_the_definition(entry: MisbehaviourReport) -> None:
    reference = [repro for repro in entry.reproductions if repro.reference_implementation]
    assert len(reference) == 1 and reference[0].runtime == "harbor"
    # And nothing requires it: strip Harbor out and the report is still valid.
    without = entry.model_copy(
        update={"reproductions": tuple(r for r in entry.reproductions if r.runtime != "harbor")}
    )
    assert validator.validate(without).ok, validator.validate(without).render()


def test_only_one_implementation_can_be_the_reference(entry: MisbehaviourReport) -> None:
    payload = entry.model_dump(mode="json")
    for repro in payload["reproductions"]:
        repro["reference_implementation"] = True
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("canonical" in problem.message for problem in result.problems)


def test_adding_a_runtime_does_not_change_the_reports_identity(entry: MisbehaviourReport) -> None:
    """The id names the behaviour, so a second runtime is appended rather than triggering a reissue."""
    added = ReproductionImplementation(
        runtime="some-other-harness",
        version="1.0.0",
        task_id="authority/refunds/denied",
        calibration=Calibration(
            reference_solution=CalibrationRun(description="Escalate instead of refunding.", score=1,
                                              evidence="runs/oracle.json"),
            null_action=CalibrationRun(description="Do nothing.", score=0, evidence="runs/nop.json"),
            calibrated_on=date(2026, 11, 11),
        ),
        status=ReproductionStatus.calibrates,
        last_verified_on=date(2026, 11, 11),
    )
    extended = entry.model_copy(update={"reproductions": (*entry.reproductions, added)})
    assert validator.validate(extended).ok, validator.validate(extended).render()
    assert extended.id == entry.id
    assert signature.digest(extended.signature) == signature.digest(entry.signature)


def test_rot_is_recorded_not_deleted(candidate: MisbehaviourReport) -> None:
    rotted = [repro for repro in candidate.reproductions if repro.status is ReproductionStatus.rot]
    assert rotted, "the worked candidate demonstrates a rotted binding"
    assert rotted[0].rot_reason
    assert rotted[0].last_verified_on is not None
    # The report is still valid, because another binding still calibrates.
    assert validator.validate(candidate).ok


def test_rot_must_say_what_stopped_calibrating(entry: MisbehaviourReport) -> None:
    payload = entry.model_dump(mode="json")
    payload["reproductions"][0]["status"] = "rot"
    payload["reproductions"][0]["rot_reason"] = ""
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("stopped calibrating" in problem.message for problem in result.problems)


def test_a_report_whose_every_binding_has_rotted_cannot_be_accepted(entry: MisbehaviourReport) -> None:
    """It still describes a real behaviour; it just cannot be newly accepted until one binding is
    re-verified or another runtime is added. The message says so."""
    payload = entry.model_dump(mode="json")
    for repro in payload["reproductions"]:
        repro["status"] = "rot"
        repro["rot_reason"] = "the runtime dropped the evidence log this task grades from"
    result = validator.validate_payload(payload)
    assert not result.ok
    assert any("still describes a real behaviour" in problem.message for problem in result.problems)


def test_a_calibrating_binding_must_say_when_it_was_last_verified(entry: MisbehaviourReport) -> None:
    payload = entry.model_dump(mode="json")
    payload["reproductions"][0]["last_verified_on"] = None
    assert not validator.validate_payload(payload).ok


def test_two_bindings_cannot_be_the_same_runtime_version_and_task(entry: MisbehaviourReport) -> None:
    payload = entry.model_dump(mode="json")
    payload["reproductions"].append(json.loads(json.dumps(payload["reproductions"][0])))
    result = validator.validate_payload(payload)
    assert not result.ok
