"""Shared engine for single-turn / trajectory judge-path inspections.

The judge-path P- and S-series inspections (P13, S02, P02, …) repeat the same
non-trivial machinery: a deterministic seeded (item × user) selection, a 3-sample
majority vote that stabilises a single judge, a binary clean-fraction score over a
diagnostics-excluded probe set, a temperature-0 determinism guard, and a
fixture-requirements gate. That logic is identical across them and is collected here
once so a new inspection composes it instead of re-deriving it (and so a fix lands in
one place). Inspection-specific pieces — the corpus schema, the rubric oracle, the
evidence-item text — stay in each runner.

No I/O and no global state: every function is pure or async-pure over its arguments.
The `pipeline` argument is the duck-typed `EvaluationPipeline` (kept as `object` to
avoid a hard import cycle), exactly as the runners already treat it.
"""

import asyncio
import random
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, TypeVar

import yaml

from ifixai.core.provider_call import send_governed
from ifixai.core.types import (
    AnalyticRubric,
    ChatMessage,
    ConversationPlan,
    EvaluationCriteria,
    EvidenceItem,
    PipelineResult,
    ProviderConfig,
    TestResult,
    TestStatus,
)
from ifixai.harness.base import build_system_message
from ifixai.inspections.fanout import raise_first_error
from ifixai.providers.base import ChatProvider, ProviderEmptyContentError
from ifixai.rules.loader import RuleLoadError
from ifixai.utils.template_renderer import render

# Default judge samples for a single (non-ensemble) judge: a majority over an odd count stabilises the
# verdict against residual single-judge variance without masking a real split (recorded as dispersion for audit).
DEFAULT_JUDGE_SAMPLES: int = 3

_SpecT = TypeVar("_SpecT")


class JudgeDispersion(TypedDict):
    """How the 3-sample majority split, attached to evidence for audit."""

    passes: int
    total: int


class TrajectoryTurnRecord(TypedDict):
    """One turn in a recorded trajectory, stored verbatim in evidence details for audit."""

    turn: int
    user_msg: str
    assistant_msg: str


class TrajectoryDetails(TypedDict, total=False):
    """Base evidence-item details payload for a scored trajectory inspection.

    Shared by P11/P13/P14/P15; per-runner TypedDicts inherit from this and add
    their own id field (seed_id or scenario_id). total=False because
    judge_dispersion is present only on the non-ensemble (3-sample) path.
    """

    category: str
    severity: str
    user: str
    turn_count: int
    trajectory: list[TrajectoryTurnRecord]
    judge_dispersion: JudgeDispersion


class ConsensusResult(TypedDict):
    """The chosen pipeline result plus, on the multi-sample path, its dispersion.

    `dispersion` is None on the ensemble path (the ensemble already aggregates its
    own samples, so there is no per-call majority to record)."""

    result: PipelineResult
    dispersion: JudgeDispersion | None


async def evaluate_with_consensus(
    pipeline: object,
    response: str,
    criteria: EvaluationCriteria,
    rubric: AnalyticRubric,
    context: str,
    context_vars: dict[str, str] | None = None,
    samples: int = DEFAULT_JUDGE_SAMPLES,
) -> ConsensusResult:
    """One judge call for an ensemble; a `samples`-way majority for a single judge.

    The majority vote stabilises a single judge's verdict on the same input run to
    run (the only residual non-determinism on the judge path once temperature is 0).
    An ensemble already aggregates internally, so it takes the single-call path and
    reports no dispersion.

    The samples are independent by construction, so they are issued concurrently:
    run serially they tripled the latency of every evidence item on this path (24
    inspections use it) for no accuracy gain. Determinism is preserved because
    `gather` returns results in input order, so the majority vote and the verdict
    body selected from it are identical to the serial implementation's. The run's
    in-flight ceiling (`send_governed`) bounds the extra concurrency.
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
        return ConsensusResult(result=result, dispersion=None)

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
    passes = sum(1 for r in results if r.passed)
    majority_passed = passes > len(results) // 2
    dispersion = JudgeDispersion(passes=passes, total=len(results))
    chosen = next((r for r in results if r.passed == majority_passed), results[0])
    final = chosen.model_copy(update={"passed": majority_passed})
    return ConsensusResult(result=final, dispersion=dispersion)


def select_specs(
    specs: list[_SpecT],
    sort_key: Callable[[_SpecT], object],
    seed: int,
    max_specs: int,
) -> list[_SpecT]:
    """Return `specs` in a deterministic order, subsampled to `max_specs` if needed.

    Within the cap the sorted list is returned with NO RNG (the P08 enumeration model
    — fully deterministic without a seed). Above the cap a `random.Random(seed)`
    subsample is drawn and then re-sorted, so a given seed yields the same set AND the
    same order every run. `sort_key` must induce a total order over the specs for the
    sort to be stable across runs.
    """
    ordered = sorted(specs, key=sort_key)  # type: ignore[arg-type, return-value]
    if len(ordered) <= max_specs:
        return ordered
    rng = random.Random(seed)
    chosen = rng.sample(ordered, max_specs)
    chosen.sort(key=sort_key)  # type: ignore[arg-type]
    return chosen


def scored_items(
    evidence: list[EvidenceItem],
    exclude_prefixes: tuple[str, ...],
) -> list[EvidenceItem]:
    """The single definition of "counts toward the binary score": judge-scored items
    (no extraction error) whose id does not start with a diagnostic prefix.

    Shared by every caller (compute_score, the score breakdown, the post-run
    sufficiency re-check, the diagnostics) so they can never disagree on the scored
    set — the bug class that silently mis-scores an inspection.
    """
    return [
        e
        for e in evidence
        if e.extraction_error is None
        and not e.test_case_id.startswith(exclude_prefixes)
    ]


def binary_clean_fraction(scored: list[EvidenceItem]) -> float:
    """Fraction of scored items that passed (1.0 each), else 0.0 on an empty set.

    This is the binary fraction-of-probes rate the judge-path inspections publish: a
    probe that vetoed (mandatory dimension failed → passed=False) contributes 0.0 and
    cannot bank partial credit, so the percentage maps directly onto the pass
    criterion. Diverges from BaseTest.compute_score's continuous weighted mean by
    design; callers document the divergence and lock it with a test.
    """
    if not scored:
        return 0.0
    return sum(1.0 for e in scored if e.passed) / len(scored)


def judge_temperature_violation(pipeline: object) -> float | None:
    """Return the judge's sampling temperature when it is > 0 (a determinism risk),
    else None.

    A non-ensemble judge above temperature 0 makes verdicts drift run to run; the
    caller turns a non-None return into its own typed error so the run fails loudly
    rather than scoring non-deterministically. An ensemble judge reports None
    (`provider_temperature()` → None) and is exempt — it absorbs single-judge variance.
    """
    if pipeline is None:
        return None
    temp = pipeline.judge_temperature()  # type: ignore[attr-defined]
    if temp is not None and temp > 0.0:
        return temp
    return None


def validate_fixture_requirements(
    fixture_requirements: list[str],
    fixture: object,
    validators: dict[str, Callable[..., bool]],
    test_id: str,
) -> None:
    """Gate the run on declared fixture_requirements via explicit validators.

    Raises RuleLoadError (which the harness maps to TestStatus.ERROR with a populated
    message) when any declared requirement has no validator or fails it. Explicit
    validators rather than getattr-introspection, per the repo style rule.
    """
    missing = [
        name
        for name in fixture_requirements
        if validators.get(name) is None or not validators[name](fixture)
    ]
    if missing:
        raise RuleLoadError(
            f"{test_id} fixture missing required fields: {missing}. "
            f"definition.yaml declares fixture_requirements={fixture_requirements}"
        )


# corpus loading: generic YAML loader shared by the 13 corpus-bearing P-runners (per-runner PxxCorpusError
# subclass the base). The loader owns file/YAML/duplicate-id checks; per-entry validation stays in each runner's _parse_scenario. T003: every InspectionCorpusError is a ValueError.


class InspectionCorpusError(ValueError):
    """Malformed inspection corpus; carries inspection id and offending context.

    All per-runner PxxCorpusError classes subclass this so `isinstance(e, ValueError)`
    holds (T003 invariant) and tests that catch PxxCorpusError still pass (subclass is
    matched by pytest.raises).
    """

    def __init__(self, test_id: str, message: str) -> None:
        self.test_id = test_id
        super().__init__(f"{test_id} corpus error: {message}")


_EntryT = TypeVar("_EntryT")


def load_corpus_yaml(
    path: Path,
    test_id: str,
    parse_entry: Callable[[object, set[str]], "_EntryT"],
    entry_id: Callable[["_EntryT"], str],
    entries_key: str = "scenarios",
) -> "list[_EntryT]":
    """Load, validate, and sort a corpus YAML file.

    Raises InspectionCorpusError (or a subclass raised by parse_entry) on:
    missing file, YAML parse failure, non-dict top level, empty or missing entries list.

    `parse_entry(raw_entry, seen)` handles per-entry field validation, taxonomy membership,
    and duplicate id detection via `seen`. It raises its own PxxCorpusError (an
    InspectionCorpusError subclass) on failure. The loader sorts by `entry_id` so
    load order in the YAML file is irrelevant.
    """
    if not path.exists():
        raise InspectionCorpusError(test_id, f"corpus not found at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise InspectionCorpusError(
            test_id, f"corpus YAML parse failed: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise InspectionCorpusError(
            test_id, "corpus YAML must be a mapping at the top level"
        )
    entries = raw.get(entries_key)
    if not isinstance(entries, list) or not entries:
        raise InspectionCorpusError(
            test_id, f"corpus YAML must declare a non-empty '{entries_key}' list"
        )
    seen: set[str] = set()
    items: list[_EntryT] = []
    for entry in entries:
        items.append(parse_entry(entry, seen))
    return sorted(items, key=entry_id)


async def async_load_corpus_yaml(
    path: Path,
    test_id: str,
    parse_entry: Callable[[object, set[str]], "_EntryT"],
    entry_id: Callable[["_EntryT"], str],
    entries_key: str = "scenarios",
) -> "list[_EntryT]":
    """Async wrapper: runs `load_corpus_yaml` in a thread so corpus disk reads
    never block the event loop. Mirror the compliant runners (P16/P20) that already
    wrap their `_load_corpus` call in `asyncio.to_thread`."""
    return await asyncio.to_thread(
        load_corpus_yaml, path, test_id, parse_entry, entry_id, entries_key
    )


# multi-turn trajectory collection: shared by trajectory inspections (P13, P03, ...) -- drive each plan step
# serially, collect the transcript, format for the judge. Single-turn inspections (S02, P02) don't use it. Lives here once so a fix lands in one place.


@dataclass(frozen=True)
class TrajectoryTurn:
    """One driven turn of a multi-turn trajectory (typed, not a bare tuple)."""

    turn: int
    user_msg: str
    assistant_msg: str


class TrajectoryProviderError(Exception):
    """A provider failure on one trajectory turn, carried (with the turn index and the
    original exception) so a runner can turn it into an excluded per-trajectory item
    rather than sinking the whole sweep. ProviderEmptyContentError is NOT wrapped — it
    propagates so the run routes to INCONCLUSIVE."""

    def __init__(self, turn: int, original: Exception) -> None:
        super().__init__(str(original))
        self.turn = turn
        self.original = original


async def collect_trajectory(
    provider: ChatProvider,
    config: ProviderConfig,
    plan: ConversationPlan,
    template_vars: dict[str, str],
) -> list[TrajectoryTurn]:
    """Drive every plan step serially (turn N needs turn N-1's response in history) and
    return the transcript. run_nonce (appended to the system message) defeats deterministic
    prompt caching by a hostile provider even at temperature 0. ProviderEmptyContentError
    propagates; any other provider error is wrapped in TrajectoryProviderError."""
    history: list[ChatMessage] = []
    system_content = build_system_message(config.system_prompt, config.run_nonce)
    if system_content is not None:
        history.append(ChatMessage(role="system", content=system_content))
    trajectory: list[TrajectoryTurn] = []
    for step in plan.steps:
        prompt = render(step.prompt_template, template_vars)
        history.append(ChatMessage(role="user", content=prompt))
        try:
            response = await send_governed(provider, history, config)
        except ProviderEmptyContentError:
            raise
        except Exception as exc:
            raise TrajectoryProviderError(step.step_id, exc) from exc
        history.append(ChatMessage(role="assistant", content=response))
        trajectory.append(
            TrajectoryTurn(turn=step.step_id, user_msg=prompt, assistant_msg=response)
        )
    return trajectory


def format_trajectory(trajectory: list[TrajectoryTurn]) -> str:
    """Render a transcript (or a leading prefix of one) into the TURN-k blocks the judge sees."""
    if not trajectory:
        return "(empty trajectory)"
    blocks = [
        f"TURN {t.turn}\nUSER: {t.user_msg}\nASSISTANT: {t.assistant_msg}"
        for t in trajectory
    ]
    return "\n\n".join(blocks)


# shared trajectory-family helpers: previously P11-P15 carried private copies (the duplication caused a
# correctness drift, P13's _correct_sufficiency missing until T001). Promoted here so a fix lands once.


def require_temperature_zero_judge(
    pipeline: object,
    test_id: str,
    error_cls: type[Exception],
) -> None:
    """Raise error_cls when the judge is non-ensemble with temperature != 0.

    A non-zero temperature is the only residual non-determinism source on the
    judge path; failing loudly prevents silent run-to-run verdict drift. Ensemble
    judges aggregate their own samples and are exempt.
    """
    temp = judge_temperature_violation(pipeline)
    if temp is not None:
        raise error_cls(
            f"{test_id} requires judge temperature=0 for deterministic probe scoring; "
            f"got temperature={temp}. Set temperature=0 on the judge provider config."
        )


def effective_seed(configured_seed: int | None) -> int:
    """Return configured_seed when pinned, otherwise a fresh cryptographic random integer.

    The per-runner _effective_seed methods differ only in which PipelineConfig field
    they read. Keep that one-liner in the runner; call this for the fallback logic.
    """
    if configured_seed is not None:
        return configured_seed
    return secrets.randbelow(2**31)


def correct_sufficiency(
    result: TestResult,
    min_items: int,
    exclude_prefixes: tuple[str, ...],
    test_id: str,
    unit_noun: str = "trajectories",
) -> TestResult:
    """Re-check the evidence floor against scored items only; flip to INCONCLUSIVE if short.

    BaseTest.execute counts every extraction-error-free item (including diagnostics)
    toward its sufficiency check. This re-checks against the scored subset only and
    flips a borderline PASS/FAIL to INCONCLUSIVE when provider errors dropped the
    real count below min_items. ERROR and INCONCLUSIVE results pass through untouched.
    """
    if result.status not in (TestStatus.PASS, TestStatus.FAIL):
        return result
    scored = scored_items(result.evidence, exclude_prefixes)
    if len(scored) >= min_items:
        return result
    return result.model_copy(
        update={
            "status": TestStatus.INCONCLUSIVE,
            "passed": False,
            "passing": False,
            "insufficient_evidence": True,
            "error_message": (
                f"{test_id} scored only {len(scored)} {unit_noun} after provider errors; "
                f"minimum {min_items} required (diagnostics excluded from the floor)."
            ),
        }
    )
