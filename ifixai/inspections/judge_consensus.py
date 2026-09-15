"""The shared multi-sample judge majority — the part of the judge path that touches no SUT.

Split out of `judge_probe`, which re-exports every name here, because `judge_probe` also owns
`collect_trajectory` and therefore a send path to the customer's agent. An inspection declared
no-SUT-contact (V08) can grade through the shared majority by importing this module without
naming one that can reach the agent. Nothing here imports a provider or a transport; the only
outbound call is `pipeline.evaluate`.
"""

import asyncio
from collections.abc import Callable
from typing import TypedDict

from ifixai.core.types import (
    AnalyticRubric,
    EvaluationCriteria,
    PipelineResult,
)
from ifixai.inspections.fanout import raise_first_error

# Default judge samples for a single (non-ensemble) judge: a majority over an odd count stabilises the
# verdict against residual single-judge variance without masking a real split (recorded as dispersion for audit).
DEFAULT_JUDGE_SAMPLES: int = 3


class JudgeDispersion(TypedDict):
    """How the 3-sample majority split, attached to evidence for audit."""

    passes: int
    total: int


class ConsensusResult(TypedDict):
    """The chosen pipeline result plus, on the multi-sample path, its dispersion.

    `dispersion` is None on the ensemble path (the ensemble already aggregates its
    own samples, so there is no per-call majority to record)."""

    result: PipelineResult
    dispersion: JudgeDispersion | None


def choose_consensus_body(
    results: list[PipelineResult], majority_passed: bool
) -> PipelineResult:
    """Pick which sample's BODY carries the majority verdict, preferring an un-errored one.

    The majority decides `passed`; this decides whose reasoning, dimension scores and
    `extraction_error` ride out with it. Every error path returns `passed=False` with an
    `extraction_error` set, so on `[error, fail, fail]` an errored sample first in input order
    would match the fail majority and donate its `extraction_error` — and callers read that field
    as "our instrument failed" and drop the item from the scored population, turning a 2-of-3
    demonstrated FAIL into an unmeasurable one. Dimension-scoped reads (`dimension_majority`)
    also depend on the published body being a clean sample.

    Priority: a CLEAN sample that voted with the majority; otherwise the first sample that voted
    with it, so an all-errored sweep still surfaces as unmeasurable. An errored sample still VOTES
    (as `passed=False`) and still counts in `total`.
    """
    clean_agreeing = [
        r
        for r in results
        if r.extraction_error is None and r.passed == majority_passed
    ]
    if clean_agreeing:
        return clean_agreeing[0]
    return next((r for r in results if r.passed == majority_passed), results[0])


async def evaluate_with_consensus(
    pipeline: object,
    response: str,
    criteria: EvaluationCriteria,
    rubric: AnalyticRubric,
    context: str,
    context_vars: dict[str, str] | None = None,
    samples: int = DEFAULT_JUDGE_SAMPLES,
    correct: Callable[[PipelineResult], PipelineResult] | None = None,
) -> ConsensusResult:
    """One judge call for an ensemble; a `samples`-way majority for a single judge.

    The majority vote stabilises a single judge's verdict on the same input run to
    run (the only residual non-determinism on the judge path once temperature is 0).
    An ensemble already aggregates internally, so it takes the single-call path and
    reports no dispersion.

    The samples are independent by construction, so they are issued concurrently:
    run serially they triple the latency of every evidence item on this path for no
    accuracy gain. Determinism is preserved because `gather` returns results in input
    order, so the majority vote and the verdict body selected from it are identical to
    the serial implementation's.

    `correct` is an OPTIONAL per-sample fixup applied to every sample BEFORE the vote, for a
    caller that can establish from its own catalogue that a dimension's verdict is not something
    the judge was entitled to decide (V02's catalogue-scoped auto-pass). It must run before the
    vote: correcting only the chosen body would leave `dispersion` describing verdicts that were
    never published, and would let uncorrected samples decide the majority. Default `None` leaves
    every other caller's behaviour untouched.
    """
    if pipeline.is_ensemble_judge():  # type: ignore[attr-defined]
        result = await pipeline.evaluate(  # type: ignore[attr-defined]
            response=response,
            criteria=criteria,
            rubric=rubric,
            references=None,
            context=context,
            context_vars=context_vars,
        )
        return ConsensusResult(
            result=correct(result) if correct is not None else result, dispersion=None
        )

    results: list[PipelineResult] = list(
        raise_first_error(
            await asyncio.gather(
                *[
                    pipeline.evaluate(  # type: ignore[attr-defined]
                        response=response,
                        criteria=criteria,
                        rubric=rubric,
                        references=None,
                        context=context,
                        context_vars=context_vars,
                    )
                    for _ in range(samples)
                ],
                return_exceptions=True,
            )
        )
    )
    if correct is not None:
        results = [correct(r) for r in results]
    passes = sum(1 for r in results if r.passed)
    majority_passed = passes > len(results) // 2
    dispersion = JudgeDispersion(passes=passes, total=len(results))
    chosen = choose_consensus_body(results, majority_passed)
    final = chosen.model_copy(update={"passed": majority_passed})
    return ConsensusResult(result=final, dispersion=dispersion)
