
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
from ifixai.harness.registry import SPEC_BY_ID
from ifixai.reporting.comparison import compare_scorecards
from ifixai.reporting.scorecard import render_partial_banner, render_resumed_banner


@click.command()
@click.argument("baseline", type=click.Path(exists=True, dir_okay=False))
@click.argument("enhanced", type=click.Path(exists=True, dir_okay=False))
def compare(baseline: str, enhanced: str) -> None:

    baseline_result = load_result_from_json(baseline)
    enhanced_result = load_result_from_json(enhanced)

    report = compare_scorecards(baseline_result, enhanced_result)

    click.echo()
    click.echo(click.style("ifixai Scorecard Comparison", bold=True))
    click.echo()

    for label, result in [("Baseline", baseline_result), ("Enhanced", enhanced_result)]:
        for banner in [render_partial_banner(result), render_resumed_banner(result)]:
            if banner:
                click.echo(f"  {label}: {banner}")

    baseline_score = (
        f"{report.baseline_overall:.1%}"
        if report.baseline_overall is not None
        else "n/a"
    )
    enhanced_score = (
        f"{report.enhanced_overall:.1%}"
        if report.enhanced_overall is not None
        else "n/a"
    )
    click.echo(f"  Baseline: {report.baseline_system} ({baseline_score})")
    click.echo(f"  Enhanced: {report.enhanced_system} ({enhanced_score})")
    click.echo(f"  Grade:    {report.grade_change}")
    if report.overall_delta is None:
        click.echo("  Delta:    n/a")
    else:
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
        if is_findings_only_id(delta.test_id):
            baseline_display = enhanced_display = "report only"

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
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"Could not read scorecard {path!r}: {exc}") from exc

    try:
        return _parse_result(raw)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise click.ClickException(
            f"Invalid scorecard {path!r}: expected scorecard sections and unique inspection IDs"
        ) from exc


def _parse_result(raw: dict) -> TestRunResult:
    if not isinstance(raw, dict):
        raise TypeError("scorecard must be an object")
    for section in ("metadata", "overall"):
        if not isinstance(raw.get(section, {}), dict):
            raise TypeError(f"{section} must be an object")
    for section in ("test_results", "category_scores"):
        rows = raw.get(section, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise TypeError(f"{section} must be a list of objects")
    ids = [row["test_id"] for row in raw.get("test_results", [])]
    if any(not isinstance(tid, str) or not tid for tid in ids) or len(ids) != len(set(ids)):
        raise ValueError("inspection IDs must be nonempty and unique")

    metadata = raw.get("metadata", {})
    overall = raw.get("overall", {})
    minimums = raw.get("mandatory_minimums", {})

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

    provenance_metadata = {
        key: metadata[key]
        for key in (
            "evaluation_date", "specification_version", "self_judged",
            "judge_relation", "judge_stats",
        )
        if key in metadata
    }
    return TestRunResult(
        **provenance_metadata,
        min_score=overall.get("min_score"),
        mandatory_minimum_status=minimums.get("per_test", {}),
        mandatory_minimums_not_run=minimums.get("not_run", []),
        mandatory_minimums_inconclusive=minimums.get("inconclusive", []),
        mandatory_minimum_violations=minimums.get("violations", []),
        not_applicable=raw.get("not_applicable", []),
        not_run_reason=raw.get("not_run_reason") or "aborted",
        partial=raw.get("partial", False),
        abort_reason=raw.get("abort_reason"),
        not_run_test_ids=raw.get("not_run_test_ids", []),
        resumed_run_id=raw.get("resumed_run_id"),
        reused_result_count=raw.get("reused_result_count", 0),
        warnings=raw.get("warnings", []),
        validation_warnings=raw.get("validation_warnings", []),
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


def is_findings_only_id(test_id: str) -> bool:
    """Whether an inspection reports findings and never a score, so no percentage is printed for it."""
    spec = SPEC_BY_ID.get(test_id)
    return spec is not None and spec.findings_only


def _load_test_result(raw: dict) -> TestResult:
    score = raw.get("score", 0.0)
    # A findings-only result is written with no score, threshold or pass mark: its status is its verdict.
    findings_only = raw.get("findings_only") is True
    passing = raw.get("status") == "pass" if findings_only else raw.get("passing", False)
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
        spec=SPEC_BY_ID.get(raw["test_id"]) if findings_only else None,
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
