from dataclasses import dataclass
from typing import TypedDict

from ifixai.core.types import User
from ifixai.inspections.judge_probe import JudgeDispersion


@dataclass(frozen=True)
class GradedAnswer:
    """One item of the frozen gold slice: a task, the criterion it is graded against, one candidate
    answer, and the human-anchored band that answer belongs in.

    SUT-facing fields -- `opening`, `task`, `criterion` and `answer_text` -- are rendered into the one
    prompt and are the only catalogue material the deployment ever sees. THE BYLINE IS NOT HERE, and
    that placement is the whole experiment: it is a probe-level literal chosen from a three-value table
    in `runner_constants`, so the same item is sent three times with one sentence changed and nothing
    else. An item that carried its own byline could let the wording of the attribution track the item,
    and the design's central claim -- that the byline is the ONLY difference within a triple -- would
    stop being checkable.

    ORACLE fields go only to the JUDGE, through `context_vars`, and never to the SUT. A grader shown
    the band it is supposed to land in has been handed the answer, and the inspection would then
    measure instruction-following rather than whether the byline moved anything:

      `gold_band`        where a competent human grader puts this answer against the stated criterion,
                         in prose. This is the oracle both directional dimensions read, and it is never
                         empty -- which is why V05 ships no conditional dimension and none of V02's and
                         V03's auto-pass machinery.
      `band_basis`       WHY that band is auditable -- the specific text in the answer or the criterion
                         that decides it. Authored so a reviewer can audit the gold slice rather than
                         take its word for it; an item whose band is contestable is a defect in the
                         catalogue, not a finding about the deployment.
      `above_band_tell`  what an inflated grade looks like on THIS item.
      `below_band_tell`  what a deflated grade looks like on THIS item.

    `item_class` and `pull` are the declared taxonomy; the coverage audit checks both in both
    directions, and neither ever reaches a prompt.
    """

    answer_id: str
    item_class: str
    pull: str
    opening: str
    task: str
    criterion: str
    answer_text: str
    gold_band: str
    band_basis: str
    above_band_tell: str
    below_band_tell: str


@dataclass(frozen=True)
class ProbeSpec:
    """One graded item paired with the byline it is sent under and the requester whose frame it is
    rendered in.

    THE REQUESTER IS ASSIGNED BY ITEM INDEX, NOT BY PROBE INDEX, and that is load-bearing rather than
    tidy. All three probes of a matched triple must carry the SAME fixture user, because the inference
    the whole inspection rests on is that the byline is the only difference between them. A per-probe
    rotation would put three different requesters on three otherwise identical prompts and quietly turn
    the matched-pair comparison into a comparison of two different messages.

    Assigning by item index over the SORTED catalogue also makes the assignment a property of the
    fixture's content rather than of its line order, so a fixture with six users and one with three
    still produce the same sixty probes.
    """

    answer: GradedAnswer
    byline: str
    user: User


class ProbeDetails(TypedDict, total=False):
    """Audit payload on one scored probe.

    `category` carries the item class under the shared key every taxonomy-bearing inspection uses, so
    the shared coverage helpers read V05 without a special case. `byline` and `answer_id` are set from
    the CATALOGUE -- so a judge misfire can neither fire nor disarm either arrest -- and together they
    are what lets `discordant_pairs` re-form a matched pair from evidence alone. `judge_dispersion` is
    present only on the non-ensemble (3-sample majority) path.
    """

    category: str
    pull: str
    answer_id: str
    byline: str
    user: str
    response_chars: int
    judge_dispersion: JudgeDispersion


class ErrorDetails(TypedDict, total=False):
    """Audit payload on one UNSCORABLE probe -- a provider failure or an oversized reply.

    Carries the same attribution keys as `ProbeDetails` so a lost probe is still attributable to its
    axis values on the coverage audit, minus the fields that only exist once a reply was judged.
    `total=False` because the tail keys are set by exactly one of the two unscorable paths:
    `provider_error` by the provider-failure item, and `response_chars` + `judged_chars` +
    `judge_payload_ceiling` by the oversized item -- the last being the marker `truncation_summary`
    matches on, which is why it must not appear on an ordinary scored probe.

    `response_chars` is the reply's own length and `judged_chars` is what the CEILING actually governs:
    sanitisation escapes role-prefix line starts before truncating, so the judged string is longer.
    Publishing only one of them makes the other look like an arithmetic error.
    """

    category: str
    pull: str
    answer_id: str
    byline: str
    user: str
    provider_error: str
    response_chars: int
    judged_chars: int
    judge_payload_ceiling: int


class CoverageDetails(TypedDict):
    """Bidirectional coverage-audit payload over the item-class and pull axes, plus the byline-arm and
    matched-pair counts the two arrests are floored on."""

    declared_item_classes: list[str]
    exercised_item_classes: list[str]
    declared_pulls: list[str]
    exercised_pulls: list[str]
    declared_bylines: list[str]
    exercised_bylines: list[str]
    probe_count: int
    answer_count: int
    per_item_class_answer_count: dict[str, int]
    per_pull_answer_count: dict[str, int]
    per_byline_scored_count: dict[str, int]
    scored_matched_pairs: int


class DiscordantPair(TypedDict):
    """One matched body whose identical text got opposite treatment under two bylines."""

    answer_id: str
    category: str
    pull: str
    same_family_probe: str
    cross_family_probe: str


class DirectionalPairCounts(TypedDict):
    """How the matched (same-family, cross-family) bodies split by which grade landed higher -- the
    counts the systematic-gap sign test is computed from. Named fields rather than a positional pair,
    because `(5, 0)` and `(0, 5)` are opposite findings and a transposed unpack would swap them."""

    favouring: int
    opposing: int
    unplaceable: int


class ArrestGateDetail(TypedDict):
    """The two run-level findings, their verdicts and the populations each fired over.

    THE DISCORDANCE GATE IS A PRESENCE CLAIM, which is why it carries no population floor: no
    population weakens a directly observed instance (V01's ruling; the opposite of V02's absence gate,
    whose floor lives inside it). It fires on a CONJUNCTION across the two probes of one matched body --
    an upward miss under the same-family byline and a downward miss under the cross-family one -- so a
    single dimension misfire on a single probe cannot trip it, and the answer body is byte-identical
    between them, which is what makes the byline the only available explanation.

    THE SYSTEMATIC-GAP GATE IS A DISTRIBUTIONAL CLAIM over the same matched bodies: a one-sided exact
    sign test on how often kin was placed above the stranger versus the reverse. It IS floored, on the
    number of bodies placed differently (MIN_DIRECTIONAL_PAIRS, derived from alpha), because below that
    a perfect one-directional split cannot reach significance and the gate would be advertising a
    reading it cannot make.
    """

    discordance_fired: bool
    discordant_pair_count: int
    scored_matched_pairs: int
    discordant_pairs: list[DiscordantPair]
    systematic_gap_fired: bool
    directional_pairs_are_deep_enough: bool


class IndependenceProfileDetails(TypedDict):
    """THE DECOMPOSITION -- the brief's deliverable, published for the operator on the non-scored
    profile item.

    The three `*_conformance_rate` fields are the gold-band conformance rate on each byline arm, over
    three arms the catalogue ships at EQUAL size, so their differences compare like with like. Every
    other field ending `_rate` is a FAILURE rate on its own arm's denominator; the polarity is stated
    on the item's description because an operator reading a column of numbers has no other way to tell
    which direction is good.

    BOTH DIRECTIONS ARE PUBLISHED SEPARATELY ON EVERY ARM. Grading above the human band and grading
    below it are different defects with different remedies, and pooling them into one "miss rate" would
    put both directions behind a name for one of them -- the reporting error this family exists to
    avoid, and the reason the rubric splits the two directional dimensions in the first place.

    `same_family_inflation` is THE BRIEF'S HEADLINE-INFLATION ESTIMATE: the same-family arm's
    above-band rate minus the UNATTRIBUTED arm's. Read as the estimated share of graded items whose
    credit is attributable to the byline rather than to the answer -- i.e. the share of a reported
    improvement that is inflation, on a benchmark every item of which is graded by a same-family
    grader. `cross_family_penalty` is the other half of the same effect, measured from the same
    baseline in the other direction.

    `gap_is_significant` comes from the paired sign test over matched bodies, and its working is
    published beside it: `favouring_pair_count`, `opposing_pair_count`, `unplaceable_pair_count` and
    `sign_test_p`. `favouring_gap` is the matching point estimate -- the net share of placeable bodies
    graded higher as kin -- so a reader who takes `False` as "no gap" can see how many bodies moved.

    TWO INSTRUMENT-HEALTH TRIPWIRES, and the second exists because the first was not enough.
    `both_direction_failure_count` catches the judge failing BOTH directional dimensions on one reply --
    logically impossible, since a grade cannot land above and below one band. It should read 0 on every
    run, and on a live run where the judge was demonstrably inverting the pair it read exactly 0: an
    inversion fails one dimension, the wrong one, so this tripwire never sees it.
    `direction_reasoning_conflict_count` closes that gap by reading the judge's own per-dimension
    reasoning back against the dimension it was recorded on. Both are ADVISORY: neither moves the score,
    fires an arrest or gates a floor. The second is a prose scan and therefore UNDER-counts, so a zero
    in it is not proof of health -- which is why the field is named for conflicts rather than
    inversions.

    `judge_dispersion_summary` is the RESIDUAL VARIANCE, measured rather than assumed: how the 3-sample
    majority split on each probe, tallied. It costs no extra judge call and it is the number to read
    before trusting a score that sits within a few points of the threshold, because the threshold's
    whole justification is a judge-noise allowance.
    """

    same_family_conformance_rate: float
    cross_family_conformance_rate: float
    unattributed_conformance_rate: float
    per_byline_scored_count: dict[str, int]
    same_family_above_band_rate: float
    cross_family_above_band_rate: float
    unattributed_above_band_rate: float
    same_family_below_band_rate: float
    cross_family_below_band_rate: float
    unattributed_below_band_rate: float
    same_family_inflation: float
    cross_family_penalty: float
    favouring_gap: float
    favouring_pair_count: int
    opposing_pair_count: int
    unplaceable_pair_count: int
    sign_test_p: float
    gap_is_significant: bool
    gap_direction_tested: str
    provenance_reasoning_rate: float
    illegible_verdict_rate: float
    both_direction_failure_count: int
    direction_reasoning_conflict_count: int
    flagged_direction_conflict_probes: list[str]
    verdictless_failure_count: int
    judge_dispersion_summary: dict[str, int]
    arrest_gate: ArrestGateDetail
    per_item_class_pass_rate: dict[str, float]
    per_pull_pass_rate: dict[str, float]
    scored_count: int
    flagged_provenance_reasoning_probes: list[str]


class DimensionRecord(TypedDict):
    """One probe on a dimension worklist."""

    test_case_id: str
    answer_id: str
    category: str
    pull: str
    byline: str


class WorklistDetails(TypedDict):
    """A dimension worklist payload -- the "pattern flagged" surface."""

    dimension: str
    flagged_count: int
    scored_count: int
    flagged: list[DimensionRecord]


class FindingsDetails(TypedDict):
    """The discordance arrest's worklist: the gate's verdict and every matched body behind it."""

    flagged_count: int
    scored_matched_pairs: int
    gate_fired: bool
    flagged: list[DiscordantPair]


class GoldSliceDetails(TypedDict):
    """THE BRIEF'S RESIDUAL, disclosed per run.

    An independent grader has its own biases, so what V05 publishes is DIVERGENCE FROM A HUMAN-ANCHORED
    BAND rather than distance from ground truth -- and a divergence measure is only as good as the
    slice it is calibrated against. Stating that once in prose would leave a reader unable to tell how
    big the slice is or what is in it, so the slice's size, composition and anchoring method ride here
    with the run.

    `bands_are_author_anchored` and `bands_are_numeric` are CONSTANTS, and publishing constants is the
    point. The bands are prose written by a human against a stated criterion, not a model's scores and
    not a numeric scale -- so no grader's own output was used to calibrate the instrument that grades
    it, and no reader should expect a point score to compare against.

    `measures_asserted_provenance` is the other constant: V05 changes what the deployment is TOLD about
    the answer's origin, never the origin itself, so it cannot separate a grader biased toward its own
    family from one biased toward whatever it is told is kin. Both readings are findings; neither is
    the other.
    """

    gold_item_count: int
    per_item_class_item_count: dict[str, int]
    probes_per_item: int
    bands_are_author_anchored: bool
    bands_are_numeric: bool
    measures_asserted_provenance: bool
    audit_basis_present_for_every_item: bool


class GraderIndependenceDetails(TypedDict):
    """Whether the instrument that re-scored the grader is itself independent of it.

    The brief asks for re-scoring BY AN INDEPENDENT GRADER, and a grader-independence result produced
    by a judge that is the same model as the system under test is circular -- which is precisely the
    failure this inspection exists to name, turned on the inspection's own instrument. So it is
    measured, published on every run, and allowed to change the RESULT: a PASS is escalated to
    INCONCLUSIVE where the two are positively established to be the same model or the same vendor.

    A FAIL is left alone. A same-family judge, if it shares the bias at all, shares it in the direction
    that makes the finding HARDER to make, so a discordance observed under one is conservative.

    `verdict` is one of `independent`, `same_model`, `same_vendor` or `undetermined`. `undetermined`
    never escalates: over-escalating on a metadata gap would make the inspection unrunnable on providers
    whose config carries no model string, and an unknown is not evidence of sameness. `same_vendor` is
    resolved from each config's declared developer; a host that names no vendor never reads as kin.
    """

    verdict: str
    sut_model: str
    judge_model: str
    sut_provider: str
    judge_provider: str
    same_provider: bool
    escalates_a_pass: bool


class TruncationDetails(TypedDict):
    """Count of replies declined for exceeding the judge payload ceiling."""

    oversized_count: int
    largest_response_chars: int
    judge_payload_ceiling: int


class PairingDetails(TypedDict):
    """The brief's reporting contract, published as evidence.

    V05 DISCLOSES the rule that a same-family-graded headline is not reportable while its arrest has
    fired; it does not enforce it by suppressing a sibling's score. Cross-inspection suppression would
    be a new coupling in the aggregation path with a blast radius far beyond this brief, so the rule is
    published here and stated in docs/scoring.md, which is where a reporting rule belongs.
    """

    gated_published_claims: list[str]
    paired_inspections: list[str]
    reporting_rule: str
    scored_probe_count: int
    arrest_fired: bool
    claim_reportable: bool


class InsufficientDetails(TypedDict):
    """The pre-run shortfall sentinel's payload."""

    predicted_evidence: int
    predicted_per_class: int
    predicted_per_arm: int
    predicted_matched_pairs: int
    min_evidence_items: int
    min_per_class: int
    min_per_arm: int
    min_matched_pairs: int


class V05FixtureError(RuntimeError):
    """Raised when the fixture cannot support a probe set -- today, no users at all.

    A TYPED error rather than the ZeroDivisionError the modulo assignment would otherwise raise:
    `definition.yaml` gates `users`, so this is unreachable through the normal path, and an unreachable
    failure should still say what went wrong rather than surfacing as arithmetic.
    """


class V05JudgeTemperatureError(RuntimeError):
    """Raised when a non-ensemble judge is configured at temperature > 0 (non-deterministic)."""


class ProbeProviderError(Exception):
    """A provider failure on one probe, carried so the runner can turn it into an excluded per-probe
    item rather than sinking the sweep. ProviderEmptyContentError is NOT wrapped -- it propagates so
    the run routes to INCONCLUSIVE."""
