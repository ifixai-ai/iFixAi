"""Shared engine for single-turn / trajectory judge-path inspections.

The judge-path inspections repeat the same non-trivial machinery: a deterministic
seeded (item × user) selection, a 3-sample majority vote that stabilises a single
judge, a binary clean-fraction score over a diagnostics-excluded probe set, a
temperature-0 determinism guard, and a fixture-requirements gate. Each of P13, P22,
P27, P32 and S02 carries its own copy today; this module collects the logic once so a
new inspection composes it instead of re-deriving it (and so a fix lands in one
place). M12 is its first consumer. Inspection-specific pieces — the corpus schema,
the rubric oracle, the evidence-item text — stay in each runner.

No I/O and no global state: every function is pure or async-pure over its arguments.
The `pipeline` argument is the duck-typed `EvaluationPipeline` (kept as `object` to
avoid a hard import cycle), exactly as the runners already treat it.
"""

import math
import random
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TypedDict, TypeVar

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
from ifixai.evaluation.proportion_ci import wilson_interval, z_for_confidence
from ifixai.harness.base import build_system_message
from ifixai.inspections.judge_consensus import (
    DEFAULT_JUDGE_SAMPLES,
    ConsensusResult,
    JudgeDispersion,
    evaluate_with_consensus,
)
from ifixai.providers.base import ChatProvider, ProviderEmptyContentError
from ifixai.rules.loader import RuleLoadError
from ifixai.scoring.unscored_pass import unscored_pass_corrected
from ifixai.utils.template_renderer import render

# The shared multi-sample judge majority lives in `judge_consensus` so a no-SUT-contact inspection
# can import it without naming this module, which owns `collect_trajectory`. Re-exported here so
# every existing importer keeps working, and named in `__all__` so `ruff --fix` cannot strip it.
__all__ = [
    "DEFAULT_JUDGE_SAMPLES",
    "PREFIX_LOCALIZATION_CAPPED",
    "PREFIX_LOCATED",
    "PREFIX_UNLOCATED_EXTRACTION_ERROR",
    "PREFIX_UNLOCATED_HOLISTIC",
    "ConsensusResult",
    "JudgeDispersion",
    "PrefixLocation",
    "TrajectoryDetails",
    "TrajectoryProviderError",
    "TrajectoryTurn",
    "TrajectoryTurnRecord",
    "binary_clean_fraction",
    "collect_trajectory",
    "correct_sufficiency",
    "effective_seed",
    "evaluate_with_consensus",
    "format_trajectory",
    "judge_temperature_violation",
    "locate_first_failing_prefix",
    "minimum_detectable_gap",
    "paired_sign_test_p",
    "require_temperature_zero_judge",
    "scored_items",
    "select_specs",
    "smallest_conclusive_pair_count",
    "validate_fixture_requirements",
    "wilson_ci_strictly_below",
]

_SpecT = TypeVar("_SpecT")


class TrajectoryTurnRecord(TypedDict):
    """One turn in a recorded trajectory, stored verbatim in evidence details for audit."""

    turn: int
    user_msg: str
    assistant_msg: str


class TrajectoryDetails(TypedDict, total=False):
    """Base evidence-item details payload for a scored trajectory inspection.

    Per-runner TypedDicts inherit from this and add their own id field (arc_id,
    seed_id, scenario_id). total=False because judge_dispersion is present only on the
    non-ensemble (3-sample) path.
    """

    category: str
    severity: str
    user: str
    turn_count: int
    trajectory: list[TrajectoryTurnRecord]
    judge_dispersion: JudgeDispersion


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


# two-group significance: the non-overlapping-Wilson-CI method (docs/scoring.md § Minimum detectable
# effect). Lives here so a caller composes it instead of carrying a private copy of the interval
# arithmetic that a fix would then have to reach twice.


def wilson_ci_strictly_below(
    lower_group: list[EvidenceItem],
    higher_group: list[EvidenceItem],
    confidence_level: float,
) -> bool:
    """True iff `lower_group`'s Wilson CI sits ENTIRELY below `higher_group`'s.

    The conservative two-group significance test: it fires only on a large, robust gap, so a caller
    using it as a gate UNDER-flags rather than false-alarms. Reuses the scorecard's own
    `wilson_interval` rather than re-deriving the arithmetic.

    Returns False when EITHER group is empty. A comparison against nothing is not evidence of no
    difference, so callers must not read False as "no effect" in that case — each one pairs this
    with an evidence floor that turns an empty group into INCONCLUSIVE.
    """
    if not lower_group or not higher_group:
        return False
    z = z_for_confidence(confidence_level)
    low = wilson_interval(
        sum(1 for e in lower_group if e.passed), len(lower_group), z
    )
    high = wilson_interval(
        sum(1 for e in higher_group if e.passed), len(higher_group), z
    )
    return low["upper"] < high["lower"]


def minimum_detectable_gap(
    n_a: int, n_b: int, confidence_level: float = 0.95
) -> float:
    """Conservative (worst-case) gap between two groups' pass rates that `wilson_ci_strictly_below`
    can detect.

    Computed at p=0.5, where the Wilson interval is widest and the minimum detectable effect is
    therefore largest. Callers publish it beside their verdict so a reader takes a negative result
    as "no effect larger than this", never as "no effect". Returns 1.0 when either group is empty
    (no detection possible).
    """
    if n_a <= 0 or n_b <= 0:
        return 1.0
    z = z_for_confidence(confidence_level)
    ci_a = wilson_interval(n_a // 2, n_a, z)
    ci_b = wilson_interval(n_b // 2, n_b, z)
    half_a = (ci_a["upper"] - ci_a["lower"]) / 2.0
    half_b = (ci_b["upper"] - ci_b["lower"]) / 2.0
    return round(half_a + half_b, 4)


# paired significance: the PAIRED counterpart of `wilson_ci_strictly_below`. The two-group form pays
# for between-ITEM variance twice; when two arms are matched — the same case, the same requester,
# everything but the manipulated variable — that variance cancels and only the split of DISCORDANT
# pairs carries information. Under the null each discordant pair is a fair coin, so the exact
# one-sided sign test is the right instrument. `math.comb` is integer arithmetic, so a gate built on
# it fires identically on every machine.


def paired_sign_test_p(favouring: int, opposing: int) -> float:
    """One-sided exact sign-test p-value for `favouring` of `favouring + opposing` discordant pairs.

    The probability, under a fair-coin null, of seeing AT LEAST this many pairs fall in the named
    direction. A caller gates on `p <= alpha` and must pass the direction it cares about as
    `favouring`.

    Returns 1.0 on an empty discordant set — no evidence is not evidence of no effect, and 1.0 is the
    value that makes every `p <= alpha` gate correctly refuse to fire on nothing.
    """
    total = favouring + opposing
    if total <= 0:
        return 1.0
    tail = sum(math.comb(total, k) for k in range(favouring, total + 1))
    return tail / 2**total


def smallest_conclusive_pair_count(alpha: float) -> int:
    """The fewest discordant pairs at which `paired_sign_test_p` CAN reach `alpha`.

    The smallest `n` with `2**-n <= alpha`. Below it the test cannot reach significance however
    lopsided the split, so callers derive their floor from this rather than writing a literal.
    Raises for a non-positive alpha (a gate that can never fire is a configuration error).
    """
    if alpha <= 0.0:
        raise ValueError(
            f"alpha must be positive; got {alpha}. A non-positive alpha makes the sign test "
            "unable to fire at any population, which is a configuration error rather than a "
            "conservative setting."
        )
    count = 1
    while 2.0**-count > alpha:
        count += 1
    return count


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


# multi-turn trajectory collection: drive each plan step serially, collect the transcript, format it
# for the judge. Single-turn inspections do not use it. Lives here once so a fix lands in one place.


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
            response = await provider.send_message(history, config)
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


# shared trajectory-family helpers. The trajectory runners each carry a private copy of these; they
# live here so a new inspection composes them and a fix lands once.


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
    bound_unscored: bool = False,
) -> TestResult:
    """Re-check the evidence floor against scored items only; flip to INCONCLUSIVE if short.

    BaseTest.execute counts every extraction-error-free item (including diagnostics)
    toward its sufficiency check. This re-checks against the scored subset only and
    flips a borderline PASS/FAIL to INCONCLUSIVE when provider errors dropped the
    real count below min_items. ERROR and INCONCLUSIVE results pass through untouched.

    `bound_unscored` is OPT-IN, and the default is off so an inspection that already
    ships keeps the verdicts it publishes today. With it on, a PASS that clears the
    floor must also survive its unscored items counted as failures
    (`scoring.unscored_pass`), so shedding failing replies cannot launder a FAIL —
    the bound the V-series is scored under. Turning it on for an existing inspection
    is a grading change and has to be released as one.
    """
    if result.status not in (TestStatus.PASS, TestStatus.FAIL):
        return result
    scored = scored_items(result.evidence, exclude_prefixes)
    if len(scored) >= min_items:
        if not bound_unscored:
            return result
        return unscored_pass_corrected(
            result, partial(scored_items, exclude_prefixes=exclude_prefixes)
        )
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


# onset localization: judge trajectory PREFIXES to find the first turn at which a failure was already
# readable. The caller supplies the predicate, so a runner keyed on a single DIMENSION's verdict (V03)
# and one keyed on the whole verdict compose the same walk.

# Reason codes published beside a located turn. An onset the scan could not find is a different
# statement from an onset at the opening answer, so a caller publishes the code rather than defaulting
# a missing turn to the first one.
PREFIX_LOCATED: str = "located"
PREFIX_UNLOCATED_HOLISTIC: str = "unlocated_holistic"
PREFIX_UNLOCATED_EXTRACTION_ERROR: str = "unlocated_extraction_error"
PREFIX_LOCALIZATION_CAPPED: str = "localization_capped"


class PrefixLocation(TypedDict):
    """Where a failure first became readable, and how many judge calls finding it cost.

    `turn` is the turn count of the shortest judged PREFIX that failed, or None when the scan could
    not locate one. `reason` is one of the four codes above. `judge_calls_used` is the caller's
    running total, returned so a caller scanning several trajectories can enforce one budget across
    all of them.
    """

    turn: int | None
    reason: str
    judge_calls_used: int


async def locate_first_failing_prefix(
    pipeline: object,
    trajectory: list[TrajectoryTurn],
    prefix_turns: list[int],
    criteria: EvaluationCriteria,
    rubric: AnalyticRubric,
    context: str,
    context_vars: dict[str, str],
    is_failure: Callable[[PipelineResult], bool],
    calls_used: int,
    call_ceiling: int,
) -> PrefixLocation:
    """Walk `prefix_turns` ascending and return the first prefix whose 1-SAMPLE verdict fails.

    ONE SAMPLE, DELIBERATELY. This is a localization, not a measurement: the caller has already
    scored the whole transcript with the full majority, so callers must keep the result out of
    scoring.

    THE BUDGET IS SHARED ACROSS TRAJECTORIES. `calls_used` comes in and goes out, so a caller
    scanning many failing trajectories enforces one ceiling over all of them. When the ceiling is
    reached mid-scan the walk stops and reports `localization_capped`.

    A prefix the judge could not read (an extraction error) is SKIPPED rather than treated as a
    failure. If every prefix was unreadable the result is `unlocated_extraction_error`; if every
    prefix was read and none failed, the result is `unlocated_holistic` — the failure rests on the
    whole transcript and was not visible earlier.
    """
    scored_any = False
    for turns in prefix_turns:
        if calls_used >= call_ceiling:
            return PrefixLocation(
                turn=None,
                reason=PREFIX_LOCALIZATION_CAPPED,
                judge_calls_used=calls_used,
            )
        consensus = await evaluate_with_consensus(
            pipeline,
            format_trajectory(trajectory[:turns]),
            criteria,
            rubric,
            context,
            context_vars=context_vars,
            samples=1,
        )
        calls_used += 1
        result = consensus["result"]
        if result.extraction_error is not None:
            continue
        scored_any = True
        if is_failure(result):
            return PrefixLocation(
                turn=turns, reason=PREFIX_LOCATED, judge_calls_used=calls_used
            )
    return PrefixLocation(
        turn=None,
        reason=(
            PREFIX_UNLOCATED_HOLISTIC if scored_any else PREFIX_UNLOCATED_EXTRACTION_ERROR
        ),
        judge_calls_used=calls_used,
    )
