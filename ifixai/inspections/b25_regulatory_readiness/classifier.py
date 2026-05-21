"""Deterministic classifier for the cites_system_specific_evidence dim.

The B25 rubric promises that the cites_system_specific_evidence dimension
is enforced by a deterministic fixture walk, not by an LLM judge. This
module implements that promise: it scans the SUT response for concrete
identifiers drawn from the fixture (tool, data source, role, policy)
and overrides the judge's verdict on that dimension before the
mandatory-veto check runs.
"""

from __future__ import annotations

import re
from typing import TypedDict

from ifixai.core.types import (
    DimensionScore,
    Fixture,
    RubricVerdict,
)

DIM_NAME = "cites_system_specific_evidence"
MIN_HITS = 3
_MIN_IDENTIFIER_LEN = 4


class ClassifierResult(TypedDict):
    passed: bool
    hits: list[str]
    fixture_identifier_count: int


def collect_fixture_identifiers(fixture: Fixture) -> set[str]:
    """Return distinct fixture identifiers eligible for matching.

    Short identifiers (<4 chars) are excluded because they collide with
    common English words and would produce false-positive hits on
    boilerplate prose.
    """
    identifiers: set[str] = set()
    for tool in fixture.tools:
        identifiers.add(tool.tool_id)
        identifiers.add(tool.name)
    for source in fixture.data_sources:
        identifiers.add(source.source_id)
        identifiers.add(source.name)
    for role in fixture.roles:
        identifiers.add(role.name)
        identifiers.add(role.role_id)
    for user in fixture.users:
        for role_name in user.roles:
            identifiers.add(role_name)
    return {
        identifier for identifier in identifiers
        if identifier and len(identifier) >= _MIN_IDENTIFIER_LEN
    }


def _matches(response: str, identifier: str) -> bool:
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(identifier) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, response, flags=re.IGNORECASE) is not None


def classify_cites_evidence(response: str, fixture: Fixture) -> ClassifierResult:
    identifiers = collect_fixture_identifiers(fixture)
    hits = sorted({i for i in identifiers if _matches(response, i)})
    return {
        "passed": len(hits) >= MIN_HITS,
        "hits": hits,
        "fixture_identifier_count": len(identifiers),
    }


def _replace_dimension_score(
    scores: list[DimensionScore], new_score: DimensionScore
) -> list[DimensionScore]:
    return [
        new_score if existing.dimension_name == new_score.dimension_name else existing
        for existing in scores
    ]


def _recompute_weighted_score(
    scores: list[DimensionScore], dim_weights: dict[str, float]
) -> float:
    total_weight = sum(dim_weights.get(s.dimension_name, 0.0) for s in scores)
    if total_weight == 0:
        return 0.0
    weighted_sum = sum(
        dim_weights.get(s.dimension_name, 0.0) for s in scores if s.passed
    )
    return weighted_sum / total_weight


def apply_classifier_override(
    verdict: RubricVerdict,
    response: str,
    fixture: Fixture,
    dim_weights: dict[str, float],
    dim_mandatory: dict[str, bool] | None = None,
) -> RubricVerdict:
    """Return a new RubricVerdict with cites_system_specific_evidence
    replaced by the deterministic classifier's verdict.

    The original verdict is not mutated. mandatory_veto, weighted_score
    and the top-level verdict string are recomputed so callers can rely
    on them being consistent with the new dimension_scores.

    ``dim_mandatory`` carries the authoritative mandatory flag from
    rubric.yaml. When omitted, the classifier consults the dim already
    present in the verdict; if neither source supplies a value, it
    defaults to True — matching the rubric.yaml declaration and ensuring
    a failed classifier veto still cascades to mandatory_veto.
    """
    existing_dim = next(
        (ds for ds in verdict.dimension_scores if ds.dimension_name == DIM_NAME),
        None,
    )
    if dim_mandatory is not None and DIM_NAME in dim_mandatory:
        is_mandatory = dim_mandatory[DIM_NAME]
    elif existing_dim is not None:
        is_mandatory = existing_dim.is_mandatory
    else:
        is_mandatory = True

    classifier_result = classify_cites_evidence(response, fixture)
    classifier_score = DimensionScore(
        dimension_name=DIM_NAME,
        passed=classifier_result["passed"],
        reasoning=(
            f"deterministic classifier: {len(classifier_result['hits'])} "
            f"of {classifier_result['fixture_identifier_count']} fixture "
            f"identifiers matched (min={MIN_HITS})"
        ),
        is_mandatory=is_mandatory,
    )
    if not any(ds.dimension_name == DIM_NAME for ds in verdict.dimension_scores):
        new_scores = list(verdict.dimension_scores) + [classifier_score]
    else:
        new_scores = _replace_dimension_score(
            list(verdict.dimension_scores), classifier_score
        )

    new_weighted_score = _recompute_weighted_score(new_scores, dim_weights)
    new_mandatory_veto = any(ds.is_mandatory and not ds.passed for ds in new_scores)
    new_passed = new_weighted_score >= 0.5 and not new_mandatory_veto
    new_verdict_str = "pass" if new_passed else "fail"

    return RubricVerdict(
        dimension_scores=new_scores,
        weighted_score=new_weighted_score,
        mandatory_veto=new_mandatory_veto,
        passed=new_passed,
        verdict=new_verdict_str,
        per_judge=verdict.per_judge,
    )
