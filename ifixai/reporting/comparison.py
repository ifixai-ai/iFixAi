

from ifixai.core.types import (
    ComparisonReport,
    TestDelta,
    TestRunResult,
)


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

        base_score = base_result.score if base_result else 0.0
        enh_score = enh_result.score if enh_result else 0.0
        base_passed = base_result.passing if base_result else False
        enh_passed = enh_result.passing if enh_result else False
        name = (enh_result or base_result).name if (enh_result or base_result) else bid

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

    return ComparisonReport(
        baseline=baseline,
        enhanced=enhanced,
        baseline_system=baseline.system_name,
        enhanced_system=enhanced.system_name,
        baseline_overall=baseline.overall_score,
        enhanced_overall=enhanced.overall_score,
        overall_delta=enhanced.overall_score - baseline.overall_score,
        grade_change=f"{baseline.grade.value} → {enhanced.grade.value}",
        baseline_grade=baseline.grade,
        enhanced_grade=enhanced.grade,
        test_deltas=deltas,
        gaps_closed=gaps_closed,
        gaps_opened=gaps_opened,
        gaps_remaining=gaps_remaining,
        fixture_mismatch=fixture_mismatch,
    )
