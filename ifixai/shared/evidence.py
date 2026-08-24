"""Builders for non-scored evidence items shared across inspection runners.

An "unscorable" evidence item records that a probe or trajectory never produced a
gradeable output — almost always because the provider raised or returned None at a
capability boundary. Such an item must be `passed=False` AND carry an
`extraction_error`, which is the signal the scoring layer uses to exclude it from
both the numerator and the denominator. Getting that invariant wrong silently
mis-scores an inspection, so it lives here once instead of being re-derived in every
runner's provider-error path.
"""

from typing import Any

from ifixai.core.types import (
    EvaluationMethod,
    EvidenceItem,
    InspectionMethod,
    JudgeErrorKind,
)


def build_unscorable_item(
    *,
    test_case_id: str,
    description: str,
    prompt_sent: str,
    expected: str,
    expected_behavior: str,
    actual: str,
    evaluation_result: str,
    extraction_error: JudgeErrorKind,
    evaluation_method: EvaluationMethod,
    details: dict[str, Any],
    inspection_method: InspectionMethod = InspectionMethod.TEXT,
) -> EvidenceItem:
    """Build a non-scored EvidenceItem flagged with an `extraction_error`.

    Stamps the two invariants every unscorable item shares — `passed=False` and a
    populated `extraction_error` — so the scoring layer drops it from numerator and
    denominator. `actual` populates both `actual` and `actual_response`; every
    caller sets those to the same value. The remaining fields are the per-inspection
    description of which capability boundary failed.
    """
    return EvidenceItem(
        test_case_id=test_case_id,
        description=description,
        prompt_sent=prompt_sent,
        expected=expected,
        expected_behavior=expected_behavior,
        actual=actual,
        actual_response=actual,
        evaluation_result=evaluation_result,
        passed=False,
        inspection_method=inspection_method,
        evaluation_method=evaluation_method,
        extraction_error=extraction_error,
        details=details,
    )


def flag_diagnostics(
    evidence: list[EvidenceItem],
    exclude_prefixes: tuple[str, ...],
) -> list[EvidenceItem]:
    """Bind a runner's score-exclusion prefixes to ``EvidenceItem.is_diagnostic``.

    A runner that excludes diagnostics from its score by prefix, but leaves the flag unset,
    leaves ``harness.base`` filtering on a field nobody populates: the score denominator and the
    ``min_evidence_items`` floor then disagree, which is the drift the flag exists to prevent.
    Deriving the flag from the same prefix tuple ``compute_score`` uses makes that disagreement
    unrepresentable.

    **An item carrying an ``extraction_error`` is never flagged, whatever its id.** A
    provider-contract failure is an UNSCORABLE MEASUREMENT, not a diagnostic, and it must stay in
    the floor's measured count. With this guard a runner can pass its score-exclusion tuple
    verbatim and the error items look after themselves.

    ``EvidenceItem`` is frozen, so a flagged item is copied rather than mutated; items whose flag
    is already correct are passed through untouched. Flagging is additive — an item already
    marked diagnostic stays marked even when it matches no prefix.
    """
    flagged: list[EvidenceItem] = []
    for item in evidence:
        is_diagnostic = item.is_diagnostic or (
            item.extraction_error is None
            and item.test_case_id.startswith(exclude_prefixes)
        )
        if is_diagnostic == item.is_diagnostic:
            flagged.append(item)
        else:
            flagged.append(item.model_copy(update={"is_diagnostic": is_diagnostic}))
    return flagged
