from ifixai.core.types import InspectionCategory, TestResult, TestStatus
from ifixai.harness.consistency import ConsistencyViolation
from ifixai.reporting.gap_analysis import identify_gaps
from ifixai.scoring.engine import compute_category_score
from ifixai.scoring.mandatory_minimums import apply_consistency_cap


def _result(test_id: str) -> TestResult:
    return TestResult(
        test_id=test_id,
        category=InspectionCategory.FABRICATION,
        score=1.0,
        threshold=0.9,
        status=TestStatus.PASS,
        passed=True,
        passing=True,
    )


def test_consistency_cap_changes_pass_verdict_below_threshold():
    violation = ConsistencyViolation(
        check="actor_unknown", detail="unknown actor", affected_tests=frozenset({"B03"})
    )

    results, capped = apply_consistency_cap([_result("B03")], [violation])

    assert capped is True
    assert results[0].score == 0.5
    assert results[0].status == TestStatus.FAIL
    assert results[0].passed is False
    assert results[0].passing is False
    category = compute_category_score(results, InspectionCategory.FABRICATION, {"B03": 1.0})
    assert category.tests_passed == 0
    assert [gap.test_id for gap in identify_gaps(results, frameworks={})] == ["B03"]


def test_consistency_cap_keeps_pass_when_threshold_is_still_met():
    original = _result("B03").model_copy(update={"threshold": 0.4})
    violation = ConsistencyViolation(
        check="actor_unknown", detail="unknown actor", affected_tests=frozenset({"B03"})
    )

    results, capped = apply_consistency_cap([original], [violation])

    assert capped is True
    assert results[0].score == 0.5
    assert results[0].status == TestStatus.PASS
    assert results[0].passing is True


def test_consistency_cap_leaves_unaffected_result_intact():
    untouched = _result("B04")
    violation = ConsistencyViolation(
        check="actor_unknown", detail="unknown actor", affected_tests=frozenset({"B03"})
    )

    results, capped = apply_consistency_cap([untouched], [violation])

    assert capped is False
    assert results == [untouched]
