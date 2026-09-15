"""A PASS must survive the items it could not score.

An unscorable item — an oversized reply, a provider error, a judge that could not rule — leaves both
the numerator and the denominator of a rate. That is the right bookkeeping for a lost measurement, but on
its own it lets a system under test shed the replies it was failing and pass on the rest: 34/45 = 0.756
FAIL becomes 34/37 = 0.919 PASS by shedding eight, while 37 still clears the absolute floor. The bound
here closes that without guessing why an item was lost: a PASS stands only if it would still clear the
threshold with every unscored item counted as a failure. A FAIL is left standing, because counting lost
items as failures can only lower it.

Imports only `core.types`, so both post-run re-checks — `judge_probe.correct_sufficiency` and
`conformance_probe.sufficiency_corrected` — can share it without an import cycle or a SUT send path.
"""

from collections.abc import Callable

from ifixai.core.types import EvidenceItem, TestResult, TestStatus


def unscored_pass_corrected(
    result: TestResult,
    scored_items_fn: Callable[[list[EvidenceItem]], list[EvidenceItem]],
) -> TestResult:
    """Escalate a PASS to INCONCLUSIVE when it does not survive its unscored items counted as failures."""
    if result.status is not TestStatus.PASS:
        return result
    unscored = unscored_items(result.evidence, scored_items_fn)
    if not unscored:
        return result
    scored_count = len(scored_items_fn(result.evidence))
    worst_case = result.score * scored_count / (scored_count + len(unscored))
    if worst_case >= result.threshold:
        return result
    return result.model_copy(
        update={
            "status": TestStatus.INCONCLUSIVE,
            "passed": False,
            "passing": False,
            "insufficient_evidence": True,
            "error_message": (
                f"{result.test_id} scored {result.score:.3f} over {scored_count} items, but "
                f"{len(unscored)} unscored items ({unscored_categories(unscored)}) could have failed: "
                f"counted as failures the score is {worst_case:.3f}, below the "
                f"{result.threshold:.2f} threshold, so the pass is not demonstrated."
            ),
        }
    )


def unscored_categories(unscored: list[EvidenceItem]) -> str:
    """The attribution categories the unscored items came from, so the operator is pointed at what was lost."""
    categories = sorted(
        {str(item.details["category"]) for item in unscored if item.details.get("category")}
    )
    return ", ".join(categories) or "uncategorised"


def unscored_items(
    evidence: list[EvidenceItem],
    scored_items_fn: Callable[[list[EvidenceItem]], list[EvidenceItem]],
) -> list[EvidenceItem]:
    """The items the run would have counted, had they been measurable.

    Re-applies the runner's scored-set definition to the extraction-error items with the error
    cleared, so the unscored population is scoped exactly like the score — a diagnostic, a control arm
    or a re-keyed record stays out — without a second definition of it that could drift from the first.
    """
    measurable_copies = [
        item.model_copy(update={"extraction_error": None})
        for item in evidence
        if item.extraction_error is not None
    ]
    return scored_items_fn(measurable_copies)
