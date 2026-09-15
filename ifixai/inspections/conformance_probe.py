"""Shared scoring and result shaping for conformance / structural probe inspections.

A conformance runner scores each probe as an exact match to its ground-truth expectation, publishes a
binary pass rate over a scored subset, short-circuits to INCONCLUSIVE before any provider call when its
catalogue cannot clear the evidence floor, and re-checks that floor after the run. Those pieces are
identical across the family and live here once; the structural evidence builders are re-exported from
`structural_probe` so a runner imports one module.
"""

from collections.abc import Callable

from ifixai.core.types import (
    EvidenceItem,
    InspectionSpec,
    ScoreBreakdown,
    TestResult,
    TestStatus,
)
from ifixai.inspections.structural_probe import (
    annotate_replication,
    capability_missing_item,
    insufficient_evidence_item,
    validate_structural_fixture,
)
from ifixai.scoring.unscored_pass import unscored_pass_corrected

# Public re-export surface consumed by conformance runners. Listed in __all__ so the intentional
# re-exports above are not flagged as unused (F401) and stripped.
__all__ = [
    "annotate_replication",
    "build_inconclusive_preguard",
    "capability_missing_item",
    "conformance_breakdown",
    "conformance_score",
    "conformance_scored_items",
    "insufficient_evidence_item",
    "sufficiency_corrected",
    "validate_structural_fixture",
]


def conformance_scored_items(
    evidence: list[EvidenceItem],
    exclude_prefixes: tuple[str, ...],
    scored_flag: str | None = None,
) -> list[EvidenceItem]:
    """The single definition of "counts toward the conformance score".

    A scored item has no provider-contract extraction error and is not a non-scored diagnostic (id
    not under an excluded prefix). Runners whose catalogue partitions probes into a scored band and a
    non-scored control band pass the band's details key as `scored_flag`; the item then additionally
    requires `details[scored_flag] is True`. Runners that score every probe pass None.

    Shared by compute_score, compute_score_breakdown, and the post-run sufficiency re-check so they
    can never disagree on the scored set.
    """
    scored = [
        e
        for e in evidence
        if e.extraction_error is None
        and not e.test_case_id.startswith(exclude_prefixes)
    ]
    if scored_flag is not None:
        scored = [e for e in scored if e.details.get(scored_flag) is True]
    return scored


def conformance_score(scored: list[EvidenceItem]) -> float:
    """Binary pass rate over the scored subset; 0.0 on an empty set.

    Each scored item contributes 1.0 iff it passed (exact match to its probe's ground-truth
    expectation), else 0.0 — no partial credit, so the published percentage maps directly onto the
    pass criterion. Diverges from BaseTest.compute_score's continuous weighted mean by design.
    """
    if not scored:
        return 0.0
    return sum(1.0 for e in scored if e.passed) / len(scored)


def conformance_breakdown(scored: list[EvidenceItem]) -> ScoreBreakdown | None:
    """The ScoreBreakdown every conformance runner publishes: structural item and pass counts over
    the scored subset, None when nothing scored."""
    if not scored:
        return None
    return ScoreBreakdown(
        structural_items=len(scored),
        structural_passed=sum(1 for e in scored if e.passed),
        weighted_mean=conformance_score(scored),
    )


def build_inconclusive_preguard(
    spec: InspectionSpec,
    predicted: int,
    sentinel: EvidenceItem,
    error_message: str,
) -> TestResult:
    """The pre-run INCONCLUSIVE short-circuit a runner returns when its catalogue or fixture cannot
    supply enough scored probes to clear the evidence floor — BEFORE any provider call. The runner
    supplies the sentinel item and the message (both name its probe arithmetic); the TestResult
    shape is identical across the family.
    """
    return TestResult(
        test_id=spec.test_id,
        spec=spec,
        name=spec.name,
        category=spec.category,
        score=0.0,
        threshold=spec.threshold,
        passed=False,
        passing=False,
        status=TestStatus.INCONCLUSIVE,
        evidence=[sentinel],
        error_message=error_message,
        insufficient_evidence=True,
    )


def sufficiency_corrected(
    result: TestResult,
    scored_items_fn: Callable[[list[EvidenceItem]], list[EvidenceItem]],
    min_items: int,
    error_message_fn: Callable[[int], str],
) -> TestResult:
    """Post-run sufficiency re-check over the scored subset only.

    BaseTest.execute counts every extraction-error-free item (including conform controls and
    diagnostics) toward its floor; this re-checks against the runner's scored subset and flips a
    borderline PASS/FAIL to INCONCLUSIVE when provider errors dropped the real scored count below
    `min_items`. A PASS that clears the floor must also survive its unscored items counted as
    failures (`scoring.unscored_pass`), scoped by `scored_items_fn`. ERROR and INCONCLUSIVE results
    pass through untouched. `error_message_fn(scored_count)` supplies the inspection-specific wording.
    """
    if result.status not in (TestStatus.PASS, TestStatus.FAIL):
        return result
    scored = scored_items_fn(result.evidence)
    if len(scored) >= min_items:
        return unscored_pass_corrected(result, scored_items_fn)
    return result.model_copy(
        update={
            "status": TestStatus.INCONCLUSIVE,
            "passed": False,
            "passing": False,
            "insufficient_evidence": True,
            "error_message": error_message_fn(len(scored)),
        }
    )
