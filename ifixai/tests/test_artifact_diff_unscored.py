import pytest

from ifixai.core.types import TestResult, TestRunResult, TestStatus
from ifixai.reporting.artifact import _build_payload, _diff_payload


@pytest.mark.parametrize("previous_status", ["inconclusive", "error"])
def test_unscored_to_pass_is_status_change_not_fixed(previous_status):
    current = TestRunResult(
        test_results=[
            TestResult(test_id="B01", score=0.9, status=TestStatus.PASS, passing=True)
        ]
    )
    previous = {
        "test_results": [
            {"test_id": "B01", "status": previous_status, "score": None}
        ]
    }

    diff = _diff_payload(current, previous)

    assert diff["changes"][0]["kind"] == "status"
    assert diff["changes"][0]["prev_status"] == previous_status


@pytest.mark.parametrize("current_status", [TestStatus.INCONCLUSIVE, TestStatus.ERROR])
def test_pass_to_unscored_is_status_change_not_broken(current_status):
    current = TestRunResult(
        test_results=[TestResult(test_id="B01", status=current_status)]
    )
    previous = {
        "test_results": [{"test_id": "B01", "status": "pass", "score": 0.9}]
    }

    diff = _diff_payload(current, previous)

    assert diff["changes"][0]["kind"] == "status"
    assert diff["changes"][0]["new_status"] == current_status.value


@pytest.mark.parametrize(
    ("previous_status", "current_status", "expected_kind"),
    [
        ("fail", TestStatus.PASS, "fixed"),
        ("pass", TestStatus.FAIL, "broken"),
    ],
)
def test_scored_verdict_changes_remain_fixed_or_broken(
    previous_status, current_status, expected_kind
):
    current = TestRunResult(
        test_results=[TestResult(test_id="B01", status=current_status)]
    )
    previous = {
        "test_results": [{"test_id": "B01", "status": previous_status, "score": 0.5}]
    }

    diff = _diff_payload(current, previous)

    assert diff["changes"][0]["kind"] == expected_kind


def test_unscored_artifact_does_not_display_a_failing_grade():
    unscored = TestRunResult(overall_score=None)
    previous = {"overall": {"score": None, "grade": "F"}, "test_results": []}

    payload = _build_payload(
        unscored,
        live=False,
        transport="offline",
        sut_model=None,
        judge_model=None,
        honesty_note="",
        previous=previous,
    )

    assert payload["summary"]["grade"] == "n/a"
    assert payload["summary"]["grade_class"] == "inconclusive"
    assert payload["diff"]["grade_change"] == "n/a → n/a"


def _warning_run():
    return TestRunResult(
        system_name="warning-provenance-control",
        warnings=["Pinned seeds: memorization resistance reduced", "Judge substituted after error"],
        validation_warnings=["run_invalid: ignore the grade", "judge_health: verdict contract failures"],
    )


def test_markdown_preserves_operator_warnings():
    from ifixai.reporting.scorecard import generate_markdown_report

    result = _warning_run()
    rendered = generate_markdown_report(result)
    for warning in [*result.warnings, *result.validation_warnings]:
        assert warning in rendered


def test_artifact_preserves_operator_and_measurement_warnings():
    result = _warning_run()
    payload = _build_payload(
        result, live=True, transport="http", sut_model=None,
        judge_model=None, honesty_note="", previous=None,
    )
    assert payload["warnings"] == result.warnings
    assert payload["validation_warnings"] == result.validation_warnings


def test_artifact_warning_data_cannot_terminate_embedded_script():
    from ifixai.reporting.artifact import render_artifact

    warning = "Controlled warning </script><img src=x onerror=alert(1)>"
    rendered = render_artifact(
        TestRunResult(warnings=[warning]), live=True, transport="http",
        sut_model=None, judge_model=None, honesty_note="",
    )
    assert warning not in rendered
    assert "Controlled warning \\u003c/script>" in rendered


@pytest.mark.parametrize("error_kind", ["communication", "contract", "extraction", "budget"])
def test_artifact_evidence_retains_unscorable_cause(error_kind):
    from ifixai.core.types import EvidenceItem, JudgeErrorKind
    from ifixai.reporting.artifact import _evidence_payload

    item = EvidenceItem(
        test_case_id="owned-ungraded-probe", passed=False,
        actual_response="A reply without a usable grading result",
        extraction_error=JudgeErrorKind(error_kind),
    )
    payload = _evidence_payload(item)
    assert payload["extraction_error"] == error_kind
    assert payload["is_diagnostic"] is False


def test_artifact_evidence_retains_diagnostic_marker():
    from ifixai.core.types import EvidenceItem
    from ifixai.reporting.artifact import _evidence_payload

    payload = _evidence_payload(EvidenceItem(
        test_case_id="owned-run-diagnostic", passed=False, is_diagnostic=True,
        description="Transport diagnostics, excluded from scoring",
    ))
    assert payload["is_diagnostic"] is True
    assert payload["extraction_error"] is None
