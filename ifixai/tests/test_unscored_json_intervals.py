"""JSON must not advertise an interval around a withheld score."""

import json

import pytest

from ifixai.core.types import ConfidenceInterval, TestResult, TestRunResult, TestStatus
from ifixai.reporting.scorecard import generate_json_report


@pytest.mark.parametrize("status", [TestStatus.INCONCLUSIVE, TestStatus.ERROR])
def test_withheld_score_withholds_its_confidence_interval(status):
    result = TestResult(test_id="B19", score=0.8, status=status,
                        confidence_interval=ConfidenceInterval(lower=0.5, upper=0.95, sample_size=5))
    row = json.loads(generate_json_report(TestRunResult(test_results=[result])))["test_results"][0]
    assert row["score"] is None
    assert not {"ci_lower", "ci_upper", "confidence_interval"} & row.keys()


def test_measured_score_retains_its_interval():
    result = TestResult(test_id="B19", score=0.8, status=TestStatus.PASS,
                        confidence_interval=ConfidenceInterval(lower=0.5, upper=0.95, sample_size=20))
    row = json.loads(generate_json_report(TestRunResult(test_results=[result])))["test_results"][0]
    assert row["ci_lower"] == 0.5
    assert row["confidence_interval"]["sample_size"] == 20
