import json

import pytest

from ifixai.core.types import InspectionCategory, TestResult, TestRunResult, TestStatus
from ifixai.reporting.scorecard import generate_json_report
from ifixai.scoring.engine import compute_category_score


@pytest.mark.parametrize("statuses", [
    [TestStatus.PASS, TestStatus.ERROR],
    [TestStatus.PASS, TestStatus.INCONCLUSIVE],
    [TestStatus.ERROR, TestStatus.INCONCLUSIVE],
    [TestStatus.PASS, TestStatus.FAIL],
    [],
])
def test_json_category_preserves_engine_assessed_count(statuses):
    results = [TestResult(
        test_id=f"B{index:02}", category=InspectionCategory.FABRICATION,
        status=status, insufficient_evidence=status == TestStatus.INCONCLUSIVE,
        score=1.0 if status == TestStatus.PASS else 0.0,
        passing=status == TestStatus.PASS,
    ) for index, status in enumerate(statuses, 1)]
    category = compute_category_score(results, InspectionCategory.FABRICATION,
                                      {result.test_id: 1.0 for result in results})
    report = json.loads(generate_json_report(TestRunResult(
        test_results=results, category_scores=[category],
    )))
    exported = report["category_scores"][0]
    assert exported["test_count"] == category.test_count
    assert exported["test_ids"] == category.test_ids
    assert exported["score"] == category.score
