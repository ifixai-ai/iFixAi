"""Per-dimension and per-axis reads over a scored evidence set.

Every judge-path inspection that publishes a taxonomy profile asks the same handful of questions of
its scored items: which probes carry a given axis value, what fraction of them passed, which ones the
judge marked a named rubric dimension failed on, and which axis values produced a scored probe at
all. That logic is inspection-agnostic — it reads `EvidenceItem.passed`, `EvidenceItem.details` and
`EvidenceItem.rubric_verdict.dimension_scores` and nothing else — and it was being re-derived in each
runner, which is the drift this module exists to prevent: a runner that gets `dimension_failures`
subtly wrong publishes a per-dimension rate that does not explain the percentage beside it, and
nothing downstream catches it.

Adopted by V01 and V02, which shared seven byte-identical copies between them, and read by the rest
of the V-series.

**What stays per-inspection, and why.** The `declared` taxonomy, the diagnostic id prefixes that
define "scored", the axis names, and the TypedDict payloads the results are packed into are all
inspection-specific and stay in each runner. Only the reads are shared, and each one takes the
ALREADY-FILTERED scored list rather than raw evidence, so the single definition of "scored" stays at
the call site and this module cannot apply a second, different filter.

No I/O and no global state: every function is pure over its arguments.
"""

from ifixai.core.types import EvidenceItem

__all__ = [
    "axis_items",
    "dimension_failure_rate",
    "dimension_failures",
    "failing_all",
    "failing_any",
    "judge_dispersion_summary",
    "matched_pairs_by",
    "measured_axis_values",
    "pass_rate",
    "per_axis_pass_rate",
]


def axis_items(
    scored: list[EvidenceItem], key: str, value: str
) -> list[EvidenceItem]:
    """One taxonomy axis value's scored probes, partitioned on `details[key]`."""
    return [e for e in scored if e.details.get(key) == value]


def pass_rate(items: list[EvidenceItem]) -> float:
    """Fraction of `items` that passed. 0.0 on an empty set.

    Named for its POLARITY rather than as a bare `rate`, because inspections publish failure rates
    beside pass rates on the same evidence item and an operator reading a column of numbers has no
    other way to tell which direction is good.
    """
    if not items:
        return 0.0
    return sum(1 for e in items if e.passed) / len(items)


def per_axis_pass_rate(
    scored: list[EvidenceItem], key: str, declared: frozenset[str]
) -> dict[str, float]:
    """Pass rate per declared axis value, omitting values with no scored probe.

    Omission is deliberate: publishing 0.0 for an axis nobody measured reads as a total failure on
    that axis, which is the opposite of the truth. The coverage audit is where an unmeasured axis
    value is reported as unmeasured.
    """
    per_value: dict[str, float] = {}
    for value in sorted(declared):
        items = axis_items(scored, key, value)
        if items:
            per_value[value] = round(pass_rate(items), 4)
    return per_value


def dimension_failures(
    scored: list[EvidenceItem], dimension: str
) -> list[EvidenceItem]:
    """Scored probes whose rubric verdict marked `dimension` failed.

    Probes with no rubric verdict are absent by construction: a failure with no verdict cannot be
    attributed to a dimension. Such a probe still scores 0.0 in a binary rate, so a caller publishing
    per-dimension rates must count it separately — otherwise a reader is shown a set of rates that
    does not explain the percentage beside them.
    """
    return [
        e
        for e in scored
        if e.rubric_verdict is not None
        and any(
            d.dimension_name == dimension and not d.passed
            for d in e.rubric_verdict.dimension_scores
        )
    ]


def dimension_failure_rate(scored: list[EvidenceItem], dimension: str) -> float:
    """The fraction of `scored` on which `dimension` failed. 0.0 on an empty set.

    Callers scope `scored` to the population the dimension can actually fail on: a dimension that
    auto-passes where its oracle declares nothing is capped below 1.0 by construction if it is
    divided by the whole scored set, and it then sits beside an unconditional rate as though the two
    were comparable.
    """
    if not scored:
        return 0.0
    return len(dimension_failures(scored, dimension)) / len(scored)


def failing_any(
    scored: list[EvidenceItem], dimensions: tuple[str, ...]
) -> list[EvidenceItem]:
    """Scored probes whose rubric verdict marked ANY of `dimensions` failed.

    The query a profile makes when it splits its worklist by SIGN — the dimensions whose failure
    means one thing in one list, the dimension whose failure means the opposite in another — so that
    both directions are not pooled behind a label naming only one of them.
    """
    return [
        e
        for e in scored
        if e.rubric_verdict is not None
        and any(
            d.dimension_name in dimensions and not d.passed
            for d in e.rubric_verdict.dimension_scores
        )
    ]


def failing_all(
    scored: list[EvidenceItem], dimensions: tuple[str, ...]
) -> list[EvidenceItem]:
    """Scored probes whose rubric verdict marked EVERY one of `dimensions` failed.

    The counterpart of `failing_any`, and the query a CONJUNCTION gate makes: a run-level finding that
    fires on two independent reads agreeing about the same probe is not the same claim as one that
    fires on either of them, and a single judge misfire cannot trip it. Written here rather than in
    the one runner that needs it today, because a conjunction over dimension verdicts is exactly the
    kind of read that gets subtly wrong twice if it is derived twice.

    A probe whose verdict does not carry one of the named dimensions at all is NOT counted: the
    dimension was not marked failed, and inferring a failure from an absent score would manufacture
    the finding. Probes with no rubric verdict are absent by construction, as in `failing_any`.
    """
    return [
        e
        for e in scored
        if e.rubric_verdict is not None
        and all(
            any(
                d.dimension_name == name and not d.passed
                for d in e.rubric_verdict.dimension_scores
            )
            for name in dimensions
        )
    ]


def measured_axis_values(scored: list[EvidenceItem], key: str) -> set[str]:
    """The distinct values of one taxonomy axis that actually produced a SCORED probe.

    The single definition of "measured", read by both a coverage audit (which reports it) and a
    coverage floor (which gates on it), so the two can never disagree. Takes the scored list rather
    than raw evidence because reading an axis from the PLAN makes its leg unfailable: the plan always
    covers everything, so it could never see an axis value whose every probe died at the provider.
    (The M12 lesson.)
    """
    return {str(item.details[key]) for item in scored if item.details.get(key)}


def matched_pairs_by(
    scored: list[EvidenceItem],
    pair_key: str,
    axis_key: str,
    value_a: str,
    value_b: str,
) -> dict[str, tuple[EvidenceItem, EvidenceItem]]:
    """Re-form matched pairs from scored evidence alone: the items sharing a `details[pair_key]` that
    produced a scored probe in BOTH of an axis's two arms.

    A PAIRED comparison is a different and stronger claim than a two-group one, because the thing held
    constant between the two probes -- the case, the body, the exchange -- cancels out of the
    difference. Every inspection that makes one has to re-form its pairs from evidence first, and that
    walk is inspection-agnostic: it reads `EvidenceItem.details` and nothing else.

    Two properties callers depend on, both of them load-bearing:

      * only ids present in BOTH arms are returned, so a probe whose partner died at the provider
        silently leaves the paired population rather than pairing with nothing;
      * the mapping is built over SORTED ids, so a published list of pair findings has a stable order
        run to run.

    An item whose `pair_key` is absent or empty is skipped -- pairing on a missing key would collide
    every such item onto one bucket and invent pairs that do not exist.
    """
    arm_a = {
        str(e.details[pair_key]): e
        for e in axis_items(scored, axis_key, value_a)
        if e.details.get(pair_key)
    }
    arm_b = {
        str(e.details[pair_key]): e
        for e in axis_items(scored, axis_key, value_b)
        if e.details.get(pair_key)
    }
    return {key: (arm_a[key], arm_b[key]) for key in sorted(set(arm_a) & set(arm_b))}


def judge_dispersion_summary(scored: list[EvidenceItem]) -> dict[str, int]:
    """How the multi-sample judge majority split across a scored set, tallied.

    THE THRESHOLD OF EVERY JUDGE-PATH INSPECTION IN THIS FAMILY IS JUSTIFIED AS A JUDGE-NOISE
    ALLOWANCE, so the size of that noise should be published rather than asserted. The split is
    already recorded on every probe by `judge_consensus.evaluate_with_consensus`, so tallying it costs
    no extra judge call and turns a per-item audit field into the run-level read an operator needs
    when a score lands near the bar: a run of unanimous probes is one the allowance covers, and a run
    of 2/1 splits is one where the percentage is the wrong thing to read.

    Keys are FIXED rather than derived from the data, so a run with no splits publishes a zero instead
    of omitting the key -- which an operator would otherwise have to read as absence. On an ensemble
    judge there is no per-call majority to record and every probe lands in `ensemble_or_absent`.

    Promoted here from V03's runner after V04 needed the identical tally: it reads
    `EvidenceItem.details['judge_dispersion']` and nothing inspection-specific, and a second private
    copy is exactly the drift this module exists to prevent -- two runners publishing a field of the
    same name computed two different ways is worse than either of them not publishing it.
    """
    tally = {
        "unanimous_pass": 0,
        "unanimous_fail": 0,
        "split": 0,
        "ensemble_or_absent": 0,
    }
    for item in scored:
        dispersion = item.details.get("judge_dispersion")
        if not isinstance(dispersion, dict):
            tally["ensemble_or_absent"] += 1
            continue
        # `.get` rather than direct indexing: a malformed payload is a reporting gap, and it must
        # surface as an uncounted probe rather than as a KeyError that crashes evidence assembly for
        # the whole run.
        passes, total = dispersion.get("passes"), dispersion.get("total")
        if not isinstance(passes, int) or not isinstance(total, int) or total <= 0:
            tally["ensemble_or_absent"] += 1
            continue
        if passes == total:
            tally["unanimous_pass"] += 1
        elif passes == 0:
            tally["unanimous_fail"] += 1
        else:
            tally["split"] += 1
    return tally
