"""Legacy numeric placeholders cannot create measured improvements."""

import pytest

from ifixai.core.types import TestResult, TestRunResult, TestStatus
from ifixai.reporting.artifact import _diff_payload


@pytest.mark.parametrize("status", ["inconclusive", "error"])
@pytest.mark.parametrize("old_score", [0.0, 1.0])
def test_legacy_unscored_numeric_placeholder_is_not_a_delta(status, old_score):
    current = TestRunResult(test_results=[TestResult(
        test_id="B19", score=0.8, status=TestStatus.PASS, passing=True,
    )])
    previous = {"test_results": [{"test_id": "B19", "status": status,
                                 "score": old_score, "score_pct": f"{old_score:.0%}"}]}
    change = _diff_payload(current, previous)["changes"][0]
    assert change["kind"] == "status"
    assert change["prev_pct"] == "n/a"


def test_measured_numeric_change_is_still_reported():
    current = TestRunResult(test_results=[TestResult(
        test_id="B19", score=0.8, status=TestStatus.PASS, passing=True,
    )])
    previous = {"test_results": [{"test_id": "B19", "status": "pass",
                                 "score": 0.7, "score_pct": "70%"}]}
    assert _diff_payload(current, previous)["changes"][0]["kind"] == "improved"
