"""Builder for the taxonomy-coverage evidence item shared across inspection runners.

Every inspection declares a taxonomy — the axes, bands, rungs, or classes it claims to
measure — and exercises some subset of it on each run. A *coverage summary* is the
info-only evidence item that audits that claim in both directions against the run which
actually happened: every declared element was exercised by at least one probe, and no
probe carried an element that was never declared. It grades the harness rather than the
target, so it is excluded from the score and must never be read as a measurement.

The verdict is the one thing every inspection agrees on: coverage passes exactly when
the declared and exercised element lists match. That invariant, and the
STRUCTURAL/STRUCTURAL method stamps every coverage summary carries, live here once
instead of being re-derived in each runner — a runner that got the comparison wrong
would report a green audit over a taxonomy it never fully exercised, and nothing
downstream would catch it.

How `declared` and `exercised` are derived stays with each inspection: the sources
differ (the planned probe set vs the emitted evidence), as do the filters that select
the scored band. Only the shared verdict and envelope are built here.
"""

from typing import Any

from ifixai.core.types import EvaluationMethod, EvidenceItem, InspectionMethod


def build_coverage_summary(
    *,
    test_case_id: str,
    description: str,
    expected: str,
    actual: str,
    declared: list[str],
    exercised: list[str],
    details: dict[str, Any],
) -> EvidenceItem:
    """Build the info-only EvidenceItem recording one taxonomy-coverage audit.

    `declared` and `exercised` are both expected sorted, which makes equality between
    them the bidirectional check: nothing declared went unexercised, and nothing
    exercised was undeclared. That comparison *is* the item's `passed` verdict —
    callers hand over the two lists rather than a precomputed boolean, so no runner can
    quietly diverge from the contract. The remaining fields are the per-inspection
    prose and the coverage counts the operator reads.
    """
    return EvidenceItem(
        test_case_id=test_case_id,
        description=description,
        expected=expected,
        actual=actual,
        passed=declared == exercised,
        inspection_method=InspectionMethod.STRUCTURAL,
        evaluation_method=EvaluationMethod.STRUCTURAL,
        details=details,
    )
