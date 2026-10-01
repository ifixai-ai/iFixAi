

from ifixai.core.types import (
    ComparisonReport,
    TestDelta,
    TestResult,
    TestRunResult,
    TestStatus,
)


def _scored_value(result: TestResult | None) -> float | None:
    if result is None or result.status in {TestStatus.INCONCLUSIVE, TestStatus.ERROR}:
        return None
    return result.score


def compare_scorecards(
    baseline: TestRunResult,
    enhanced: TestRunResult,
) -> ComparisonReport:
    baseline_scores = {
        br.test_id: br for br in baseline.test_results
    }
    enhanced_scores = {
        br.test_id: br for br in enhanced.test_results
    }

    all_ids = sorted(
        set(baseline_scores.keys()) | set(enhanced_scores.keys())
    )

    deltas: list[TestDelta] = []
    gaps_closed: list[str] = []
    gaps_opened: list[str] = []
    gaps_remaining: list[str] = []

    for bid in all_ids:
        base_result = baseline_scores.get(bid)
        enh_result = enhanced_scores.get(bid)

        # Absence means that the inspection was not run. Treating it as a failed
        # zero-score result invents a regression or a newly closed gap.
        if base_result is None or enh_result is None:
            result = base_result or enh_result
            assert result is not None
            deltas.append(
                TestDelta(
                    test_id=bid,
                    test_name=result.name,
                    baseline_score=_scored_value(base_result),
                    enhanced_score=_scored_value(enh_result),
                    status_change=(
                        "missing_baseline" if base_result is None else "missing_enhanced"
                    ),
                )
            )
            continue

        base_score = _scored_value(base_result)
        enh_score = _scored_value(enh_result)
        if base_score is None or enh_score is None:
            if base_score is None and enh_score is None:
                status = "unscored_both"
            elif base_score is None:
                status = "unscored_baseline"
            else:
                status = "unscored_enhanced"
            deltas.append(
                TestDelta(
                    test_id=bid,
                    test_name=enh_result.name or base_result.name or bid,
                    baseline_score=base_score,
                    enhanced_score=enh_score,
                    status_change=status,
                )
            )
            continue

        base_passed = base_result.passing
        enh_passed = enh_result.passing
        name = enh_result.name or base_result.name or bid

        delta = enh_score - base_score

        if not base_passed and enh_passed:
            status = "fixed"
            gaps_closed.append(bid)
        elif base_passed and not enh_passed:
            status = "broken"
            gaps_opened.append(bid)
        elif delta > 0.01:
            status = "improved"
            if not enh_passed:
                gaps_remaining.append(bid)
        elif delta < -0.01:
            status = "regressed"
            if not enh_passed:
                gaps_remaining.append(bid)
        else:
            status = "unchanged"
            if not enh_passed:
                gaps_remaining.append(bid)

        deltas.append(
            TestDelta(
                test_id=bid,
                test_name=name,
                baseline_score=base_score,
                enhanced_score=enh_score,
                delta=delta,
                status_change=status,
                gap_closed=not base_passed and enh_passed,
            )
        )

    fixture_mismatch = baseline.fixture_name != enhanced.fixture_name
    baseline_overall = baseline.overall_score
    enhanced_overall = enhanced.overall_score
    overall_delta = (
        enhanced_overall - baseline_overall
        if baseline_overall is not None and enhanced_overall is not None
        else None
    )
    baseline_grade = baseline.grade.value if baseline_overall is not None else "n/a"
    enhanced_grade = enhanced.grade.value if enhanced_overall is not None else "n/a"

    return ComparisonReport(
        baseline=baseline,
        enhanced=enhanced,
        baseline_system=baseline.system_name,
        enhanced_system=enhanced.system_name,
        baseline_overall=baseline_overall,
        enhanced_overall=enhanced_overall,
        overall_delta=overall_delta,
        grade_change=f"{baseline_grade} → {enhanced_grade}",
        baseline_grade=baseline.grade,
        enhanced_grade=enhanced.grade,
        test_deltas=deltas,
        gaps_closed=gaps_closed,
        gaps_opened=gaps_opened,
        gaps_remaining=gaps_remaining,
        fixture_mismatch=fixture_mismatch,
    )
