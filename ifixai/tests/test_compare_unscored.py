import json

import pytest
from click.testing import CliRunner

from ifixai.cli.compare import compare
from ifixai.core.types import TestGrade, TestRunResult
from ifixai.reporting.comparison import compare_scorecards


@pytest.mark.parametrize(
    ("baseline_score", "enhanced_score", "expected_grade_change"),
    [
        (None, 0.8, "n/a → B"),
        (0.8, None, "B → n/a"),
        (None, None, "n/a → n/a"),
    ],
)
def test_comparison_keeps_unscored_overall_as_unknown(
    baseline_score, enhanced_score, expected_grade_change
):
    baseline = TestRunResult(
        system_name="baseline", overall_score=baseline_score, grade=TestGrade.B
    )
    enhanced = TestRunResult(
        system_name="enhanced", overall_score=enhanced_score, grade=TestGrade.B
    )

    report = compare_scorecards(baseline, enhanced)

    assert report.baseline_overall == baseline_score
    assert report.enhanced_overall == enhanced_score
    assert report.overall_delta is None
    assert report.grade_change == expected_grade_change


def test_comparison_keeps_numeric_delta():
    report = compare_scorecards(
        TestRunResult(overall_score=0.4, grade=TestGrade.D),
        TestRunResult(overall_score=0.8, grade=TestGrade.B),
    )

    assert report.overall_delta == pytest.approx(0.4)
    assert report.grade_change == "D → B"


def test_compare_cli_displays_unscored_run(tmp_path):
    baseline = tmp_path / "baseline.json"
    enhanced = tmp_path / "enhanced.json"
    baseline.write_text(
        json.dumps(
            {
                "metadata": {"system_name": "baseline"},
                "overall": {"score": None, "grade": "F"},
            }
        ),
        encoding="utf-8",
    )
    enhanced.write_text(
        json.dumps(
            {
                "metadata": {"system_name": "enhanced"},
                "overall": {"score": 0.8, "grade": "B"},
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(compare, [str(baseline), str(enhanced)])

    assert result.exit_code == 0, result.exception
    assert "Baseline: baseline (n/a)" in result.output
    assert "Enhanced: enhanced (80.0%)" in result.output
    assert "Grade:    n/a → B" in result.output
    assert "Delta:    n/a" in result.output
