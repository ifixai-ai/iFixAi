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


@pytest.mark.parametrize(
    ("status", "minimums_passed", "not_run", "label"),
    [
        (TestStatus.INCONCLUSIVE, True, ["B08"], "NOT RUN"),
        (TestStatus.INCONCLUSIVE, True, [], "INCONCLUSIVE"),
        (TestStatus.FAIL, False, [], "FAIL"),
        (TestStatus.PASS, True, [], "PASS"),
    ],
)
def test_artifact_minimums_match_evaluated_gate_state(status, minimums_passed, not_run, label):
    result = TestRunResult(
        mandatory_minimums_passed=minimums_passed,
        mandatory_minimum_status={"B08": status},
        mandatory_minimums_not_run=not_run,
    )
    payload = _build_payload(
        result, live=False, transport="offline", sut_model=None,
        judge_model=None, honesty_note="", previous=None,
    )
    assert payload["summary"]["mm_label"] == label
    assert payload["summary"]["mm_status"]["B08"] == ("not run" if not_run else status.value)


def test_artifact_native_scoped_gate_does_not_claim_pass():
    from ifixai.scoring.mandatory_minimums import check_mandatory_minimums

    checked = check_mandatory_minimums([], selected_ids={"B19"})
    result = TestRunResult(
        mandatory_minimums_passed=checked["minimums_passed"],
        mandatory_minimum_status=checked["minimum_status"],
        mandatory_minimums_not_run=checked["minimums_not_run"],
    )
    payload = _build_payload(
        result, live=False, transport="offline", sut_model=None,
        judge_model=None, honesty_note="", previous=None,
    )
    assert payload["summary"]["mm_label"] == "NOT RUN"
    assert all(value == "not run" for value in payload["summary"]["mm_status"].values())


def test_artifact_failed_minimum_takes_precedence_over_unselected_minimum():
    result = TestRunResult(
        mandatory_minimums_passed=False,
        mandatory_minimum_status={"B08": TestStatus.FAIL, "B09": TestStatus.INCONCLUSIVE},
        mandatory_minimums_not_run=["B09"],
    )
    payload = _build_payload(
        result, live=False, transport="offline", sut_model=None,
        judge_model=None, honesty_note="", previous=None,
    )
    assert payload["summary"]["mm_label"] == "FAIL"
    assert payload["summary"]["mm_status"] == {"B08": "fail", "B09": "not run"}
