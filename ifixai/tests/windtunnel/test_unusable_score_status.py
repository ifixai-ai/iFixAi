"""A numeric partial score is not a conclusive detection."""
import pytest

from ifixai.core.types import EvidenceItem, TestResult, TestStatus
from ifixai.windtunnel.measure import _classify


def _result(score=1.0, **kwargs):
    return TestResult(test_id="B01", score=score, threshold=1.0, status=TestStatus.PASS,
                      evidence=[EvidenceItem(test_case_id="observed")], **kwargs)


@pytest.mark.parametrize("side", ["clean", "defective"])
@pytest.mark.parametrize("status", [TestStatus.INCONCLUSIVE, TestStatus.ERROR])
def test_nonconclusive_status_cannot_be_a_detection(side, status):
    clean, defective = _result(), _result(0.0)
    if side == "clean":
        clean = clean.model_copy(update={"status": status})
    else:
        defective = defective.model_copy(update={"status": status})
    assert _classify(clean, defective, expected=True).outcome == "unusable"


def test_insufficient_numeric_score_is_unusable():
    result = _classify(_result(), _result(0.0, insufficient_evidence=True), expected=True)
    assert result.outcome == "unusable"


def test_conclusive_threshold_crossing_still_detects():
    defective = _result(0.0).model_copy(update={"status": TestStatus.FAIL})
    assert _classify(_result(), defective, expected=True).outcome == "detected"
