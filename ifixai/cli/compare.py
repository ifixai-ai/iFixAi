
import json

import click

from ifixai.core.types import (
    CategoryScore,
    InspectionCategory,
    TestGrade,
    TestResult,
    TestRunResult,
    TestStatus,
)
from ifixai.reporting.comparison import compare_scorecards


@click.command()
@click.argument("baseline", type=click.Path(exists=True))
@click.argument("enhanced", type=click.Path(exists=True))
def compare(baseline: str, enhanced: str) -> None:

    baseline_result = load_result_from_json(baseline)
    enhanced_result = load_result_from_json(enhanced)

    report = compare_scorecards(baseline_result, enhanced_result)

    click.echo()
    click.echo(click.style("ifixai Scorecard Comparison", bold=True))
    click.echo()

    click.echo(f"  Baseline: {report.baseline_system} ({report.baseline_overall:.1%})")
    click.echo(f"  Enhanced: {report.enhanced_system} ({report.enhanced_overall:.1%})")
    click.echo(f"  Grade:    {report.grade_change}")
    delta_sign = "+" if report.overall_delta >= 0 else ""
    click.echo(f"  Delta:    {delta_sign}{report.overall_delta:.1%}")

    if report.fixture_mismatch:
        click.echo(click.style("  Warning: fixtures differ between runs.", fg="yellow"))

    click.echo()

    header = f"{'ID':<12} {'Name':<30} {'Baseline':>8} {'Enhanced':>8} {'Delta':>8} {'Status':<16}"
    click.echo(header)
    click.echo("-" * len(header))

    for delta in report.test_deltas:
        delta_display = (
            f"{'+' if delta.delta >= 0 else ''}{delta.delta:.0%}"
            if delta.delta is not None
            else "n/a"
        )
        baseline_display = (
            f"{delta.baseline_score:.0%}" if delta.baseline_score is not None else "n/a"
        )
        enhanced_display = (
            f"{delta.enhanced_score:.0%}" if delta.enhanced_score is not None else "n/a"
        )

        status_color = _status_color(delta.status_change)
        status_styled = click.style(delta.status_change, fg=status_color)

        click.echo(
            f"{delta.test_id:<12} "
            f"{delta.test_name:<30} "
            f"{baseline_display:>8} "
            f"{enhanced_display:>8} "
            f"{delta_display:>8} "
            f"{status_styled:<16}"
        )

    click.echo()

    if report.gaps_closed:
        click.echo(click.style("Gaps closed: ", fg="green") + ", ".join(report.gaps_closed))
    if report.gaps_opened:
        click.echo(click.style("Gaps opened: ", fg="red") + ", ".join(report.gaps_opened))
    if report.gaps_remaining:
        click.echo(click.style("Gaps remaining: ", fg="yellow") + ", ".join(report.gaps_remaining))

    click.echo()


def load_result_from_json(path: str) -> TestRunResult:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)

    metadata = raw.get("metadata", {})
    overall = raw.get("overall", {})

    test_results = [_load_test_result(br) for br in raw.get("test_results", [])]

    category_scores = [
        CategoryScore(
            category=_parse_category(cs.get("category", "")),
            score=cs.get("score", 0.0),
            weight=cs.get("weight", 0.0),
            test_count=cs.get("test_count", 0),
            test_ids=cs.get("test_ids", []),
        )
        for cs in raw.get("category_scores", [])
    ]

    return TestRunResult(
        system_name=metadata.get("system_name", ""),
        system_version=metadata.get("system_version", "1.0"),
        provider=metadata.get("provider", ""),
        fixture_name=metadata.get("fixture_name", ""),
        overall_score=overall.get("score", 0.0),
        grade=_parse_grade(overall.get("grade", "F")),
        strategic_score=overall.get("strategic_score", 0.0),
        test_results=test_results,
        category_scores=category_scores,
        mandatory_minimums_passed=overall.get("mandatory_minimums_passed", False),
        run_mode=metadata.get("run_mode", "full"),
    )


def _load_test_result(raw: dict) -> TestResult:
    score = raw.get("score", 0.0)
    passing = raw.get("passing", False)
    # Exported scorecards use JSON null for inconclusive/error inspections.
    # TestResult stores a numeric score, so preserve the unscored state via
    # status while using zero only as its internal placeholder.
    status = TestStatus(
        raw.get("status")
        or ("inconclusive" if score is None else "pass" if passing else "fail")
    )
    return TestResult(
        test_id=raw["test_id"],
        name=raw.get("name", ""),
        category=_parse_category(raw.get("category", "")),
        score=0.0 if score is None else score,
        threshold=raw.get("threshold", 0.0),
        passed=passing,
        passing=passing,
        status=status,
        error=raw.get("error"),
    )


def _parse_category(value: str) -> InspectionCategory:
    for cat in InspectionCategory:
        if cat.value == value:
            return cat
    return InspectionCategory.FABRICATION


def _parse_grade(value: str) -> TestGrade:
    try:
        return TestGrade(value)
    except ValueError:
        return TestGrade.F


def _status_color(status: str) -> str:
    color_map = {
        "fixed": "green",
        "improved": "green",
        "unchanged": "white",
        "regressed": "red",
        "broken": "red",
        "missing_baseline": "yellow",
        "missing_enhanced": "yellow",
        "unscored_baseline": "yellow",
        "unscored_enhanced": "yellow",
        "unscored_both": "yellow",
    }
    return color_map.get(status, "white")
