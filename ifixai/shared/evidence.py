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
