import json

from click.testing import CliRunner

from ifixai.cli.compare import compare
from ifixai.core.types import TestResult, TestRunResult
from ifixai.reporting.comparison import compare_scorecards


def _result(test_id: str, score: float, passing: bool) -> TestResult:
    return TestResult(test_id=test_id, name=test_id, score=score, passing=passing)


def test_unmatched_inspections_are_not_reported_as_fixed_or_broken():
    baseline = TestRunResult(test_results=[_result("B01", 0.9, True)])
    enhanced = TestRunResult(test_results=[_result("B02", 0.8, True)])

    report = compare_scorecards(baseline, enhanced)

    assert report.gaps_closed == []
    assert report.gaps_opened == []
    assert report.gaps_remaining == []
    assert [(delta.test_id, delta.status_change) for delta in report.test_deltas] == [
        ("B01", "missing_enhanced"),
        ("B02", "missing_baseline"),
    ]
    assert report.test_deltas[0].enhanced_score is None
    assert report.test_deltas[1].baseline_score is None
    assert all(delta.delta is None and not delta.gap_closed for delta in report.test_deltas)


def test_shared_inspections_still_report_real_change():
    baseline = TestRunResult(test_results=[_result("B01", 0.2, False)])
    enhanced = TestRunResult(test_results=[_result("B01", 0.9, True)])

    report = compare_scorecards(baseline, enhanced)

    assert report.gaps_closed == ["B01"]
    assert report.test_deltas[0].status_change == "fixed"
    assert report.test_deltas[0].delta == 0.7


def test_compare_cli_marks_unmatched_inspection_as_unscored(tmp_path):
    baseline = tmp_path / "baseline.json"
    enhanced = tmp_path / "enhanced.json"
    baseline.write_text(
        json.dumps({"test_results": [{"test_id": "B01", "score": 0.9, "passing": True}]}),
        encoding="utf-8",
    )
    enhanced.write_text(json.dumps({"test_results": []}), encoding="utf-8")

    result = CliRunner().invoke(compare, [str(baseline), str(enhanced)])

    assert result.exit_code == 0, result.exception
    assert "B01" in result.output
    assert "n/a" in result.output
    assert "missing_enhanced" in result.output
    assert "Gaps opened" not in result.output
