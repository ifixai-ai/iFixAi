import logging
from typing import Optional

from ifixai.evaluation.proportion_ci import ProportionCI
from ifixai.core.types import (
    InspectionCategory,
    TestResult,
    TestStatus,
    CategoryScore,
    ConfidenceInterval,
    EvidenceItem,
)

_logger = logging.getLogger(__name__)


def compute_test_score(
    results: list[EvidenceItem],
    count_extraction_errors_as_fail: bool = False,
) -> float:
    if not results:
        return 0.0
    scored = (
        results
        if count_extraction_errors_as_fail
        else [item for item in results if item.extraction_error is None]
    )
    if not scored:
        return 0.0
    total = sum(
        (
            item.rubric_weighted_score
            if item.rubric_weighted_score is not None
            else (1.0 if item.passed else 0.0)
        )
        for item in scored
    )
    return total / len(scored)


def compute_category_score(
    test_results: list[TestResult],
    category: InspectionCategory,
    weights: dict[str, float],
    category_weights: dict[InspectionCategory, float] | None = None,
) -> CategoryScore:
    category_weight = category_weights.get(category, 0.0) if category_weights else 0.0

    category_results = [br for br in test_results if br.category == category]

    if not category_results:
        return CategoryScore(
            category=category,
            score=None,
            weight=category_weight,
            test_count=0,
            tests_passed=0,
            test_ids=[],
        )

    all_ids = [br.test_id for br in category_results]
    scored_results = [
        br
        for br in category_results
        if not br.insufficient_evidence
        and br.status != TestStatus.ERROR
        and not _is_exploratory(br)
        and not _is_advisory(br)
        and not _is_attestation(br)
    ]

    if not scored_results:
        return CategoryScore(
            category=category,
            score=None,
            weight=category_weight,
            test_count=0,
            tests_passed=0,
            test_ids=all_ids,
        )

    total_weight = 0.0
    weighted_sum = 0.0

    for result in scored_results:
        if result.test_id not in weights:
            _logger.warning(
                "No weight configured for %s; defaulting to 1.0. "
                "Add it to the weights dict to silence this warning.",
                result.test_id,
            )
        test_weight = weights.get(result.test_id, 1.0)
        weighted_sum += result.score * test_weight
        total_weight += test_weight

    score = weighted_sum / total_weight if total_weight > 0.0 else 0.0

    return CategoryScore(
        category=category,
        score=score,
        weight=category_weight,
        test_count=len(scored_results),
        tests_passed=sum(1 for r in scored_results if r.passing),
        test_ids=all_ids,
    )


def _is_exploratory(result: TestResult) -> bool:
    spec = result.spec
    return bool(spec and spec.is_exploratory)


def _is_advisory(result: TestResult) -> bool:
    spec = result.spec
    return bool(spec and spec.is_advisory)


def _is_attestation(result: TestResult) -> bool:
    spec = result.spec
    return bool(spec and spec.is_attestation)


def compute_overall_score(
    category_scores: list[CategoryScore],
    category_weights: dict[InspectionCategory, float],
) -> Optional[float]:
    total_weight = 0.0
    weighted_sum = 0.0

    for cs in category_scores:
        if cs.score is None:
            continue
        weight = category_weights.get(cs.category, 0.0)
        weighted_sum += cs.score * weight
        total_weight += weight

    if total_weight == 0.0:
        return None

    return weighted_sum / total_weight


def compute_strategic_score(
    test_results: list[TestResult],
    strategic_ids: list[str],
) -> float:
    strategic_results = [br for br in test_results if br.test_id in strategic_ids]

    if not strategic_results:
        return 0.0

    scored = [
        br
        for br in strategic_results
        if br.score is not None
        and not br.insufficient_evidence
        and br.status != TestStatus.ERROR
    ]
    return sum(br.score for br in scored) / len(scored) if scored else 0.0


def compute_test_ci(
    evidence: list[EvidenceItem],
    confidence_level: float = 0.95,
) -> ConfidenceInterval | None:
    return ProportionCI(confidence_level=confidence_level).compute(evidence)
