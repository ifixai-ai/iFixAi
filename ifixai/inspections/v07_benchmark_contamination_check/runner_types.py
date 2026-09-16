from dataclasses import dataclass
from typing import TypedDict

from ifixai.core.types import User
from ifixai.inspections.judge_probe import JudgeDispersion


@dataclass(frozen=True)
class ScenarioArm:
    """One arm of a matched case: a scenario, the category it belongs to, and the judge's oracle for it.

    `scenario` IS THE ONLY SUT-FACING FIELD. The actor frame and the ask are literals rendered
    byte-identically on all forty-eight probes, so the scenario is the only thing that varies and there
    is no second channel through which an arm could hint at which family it belongs to.

    `scenario_category` IS DECLARED, NOT THE ARM. The arm is DERIVED from it by `runner_split.
    classify_arm`, and `assert_split_is_filter_derived` refuses to import a catalogue in which a
    declared arm and the filter disagree. That is what makes the brief's "the split is pinned and
    reproducible" clause a property of the build rather than a promise: there is no hand-assigned arm
    anywhere to drift.

    ORACLE fields go only to the JUDGE, through `context_vars`, and never to the SUT. A deployment
    shown the move it is supposed to make has been handed the answer, and the inspection would then
    measure instruction-following:

      `required_move`   WHAT the disposition requires ON THIS SCENARIO, stated concretely enough that a
                        judge can check whether it happened rather than form its own view of what the
                        scenario calls for.
      `shortfall_tell`  what FALLING SHORT looks like on THIS scenario -- the plausible, well-written
                        reply that gestures at the move without making it.
      `overreach_tell`  what TIPPING PAST IT looks like on THIS scenario -- refusing the whole task,
                        escalating what was not anyone else's to decide, hedging everything. Live on
                        every arm, which is why no V07 dimension is conditional.
    """

    scenario_category: str
    scenario: str
    required_move: str
    shortfall_tell: str
    overreach_tell: str


@dataclass(frozen=True)
class TransferCase:
    """One matched case: the same disposition, put to the deployment in two scenario categories.

    THE DISPOSITION IS THE THING HELD CONSTANT ACROSS THE TWO ARMS, and that is the whole design. If
    the held-out arm required a DIFFERENT move, the gap between the arms would measure breadth of
    competence rather than transfer, and V07 would be a worse version of four inspections that already
    exist. `transfer_basis` records, in one sentence, why both arms demand the same move -- an authored
    claim published in auditable form rather than taken, the device V06's `paraphrase_basis` provides.
    A case whose held-out arm quietly demands something else is a defect in the catalogue, not a
    finding about the deployment.

    `difficulty` IS A PROPERTY OF THE CASE, NOT OF THE ARM, and that is a structural guarantee rather
    than a balanced spread: the two arms carry the same marker because there is only one marker, so
    declared difficulty cannot produce a transfer gap. It does exactly two things -- it scopes the
    paired-discordance arrest to `routine` cases, and it bounds the threshold from below by a count
    (design spec section 6.1). It reaches neither the SUT nor the judge.
    """

    case_id: str
    disposition: str
    difficulty: str
    transfer_basis: str
    in_category: ScenarioArm
    held_out: ScenarioArm


@dataclass(frozen=True)
class ProbeSpec:
    """One matched case paired with the arm it is sent under and the requester whose frame it renders in.

    THE REQUESTER IS ASSIGNED BY CASE INDEX, NOT BY PROBE INDEX, and that is load-bearing rather than
    tidy. Both probes of a matched case must carry the SAME fixture user, because the inference the
    whole inspection rests on is that the scenario category is the only difference between them. A
    per-probe rotation would put two different requesters on two otherwise comparable prompts and
    quietly turn the paired comparison into a comparison of two different messages -- and the paired
    sign test, which is V07's sharper arrest, rests on exactly that pairing.

    Assigning by case index over the SORTED catalogue also makes the assignment a property of the
    fixture's content rather than of its line order, so a fixture with six users and one with three
    still produce the same forty-eight probes.
    """

    case: TransferCase
    arm: str
    user: User


class ProbeDetails(TypedDict, total=False):
    """Audit payload on one scored probe.

    `category` carries the DISPOSITION under the shared key every taxonomy-bearing inspection uses, so
    the shared coverage helpers and `ScoreBreakdown.per_category_pass_rate` read V07 without a special
    case. `arm`, `case_id`, `scenario_category` and `difficulty` are set from the CATALOGUE -- so a
    judge misfire can neither fire nor disarm an arrest -- and together they are what lets the paired
    gate re-form a matched case from evidence alone. `judge_dispersion` is present only on the
    non-ensemble (3-sample majority) path.
    """

    category: str
    arm: str
    case_id: str
    scenario_category: str
    difficulty: str
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
    arm: str
    case_id: str
    scenario_category: str
    difficulty: str
    user: str
    provider_error: str
    response_chars: int
    judged_chars: int
    judge_payload_ceiling: int


class CoverageDetails(TypedDict):
    """Bidirectional coverage-audit payload over the three declared axes, plus the per-arm and
    matched-pair counts the arrests are floored on."""

    declared_dispositions: list[str]
    exercised_dispositions: list[str]
    declared_scenario_categories: list[str]
    exercised_scenario_categories: list[str]
    declared_arms: list[str]
    exercised_arms: list[str]
    probe_count: int
    case_count: int
    per_disposition_case_count: dict[str, int]
    per_arm_scored_count: dict[str, int]
    scored_routine_pairs: int


class DiscordantPair(TypedDict):
    """One matched case whose two arms disagreed.

    `direction` separates the two, because they are two different findings: `contamination` is the
    in-category arm passing where the held-out arm failed -- the arrest's direction -- and `reverse` is
    the opposite, which is published with its sign and never arrests. Pooling them would report "the
    scenario family moved the outcome" without saying which way, which is the reporting error this
    family exists to avoid.
    """

    case_id: str
    category: str
    difficulty: str
    direction: str
    in_category_probe: str
    in_category_scenario_category: str
    held_out_probe: str
    held_out_scenario_category: str


class DirectionCounts(TypedDict):
    """The two discordant-pair counts the sign test is computed from.

    Returned as one payload rather than as two values, so no caller can read one without the other --
    a contamination-direction count quoted without the reverse count beside it is a one-sided claim
    reported as if it were two-sided, which is exactly what the sign test exists to prevent.
    """

    contamination: int
    reverse: int


class ArrestGateDetail(TypedDict):
    """The two run-level findings, their verdicts and the populations each fired over.

    BOTH ARRESTS ARE FLOORED, and neither floor is a matter of taste. The transfer-gap arrest is a
    two-group RATE claim, and an interval comparison over arms the run itself declares too thin would
    report the test's own noise as a finding. The discordance arrest is a PAIRED CONCENTRATION claim,
    and its floor is the point below which the sign test cannot reach significance at all -- derived
    from alpha rather than chosen, so a gate that could never fire cannot be advertised.

    `minimum_detectable_gap` rides here rather than only on the profile, because a reader meeting
    `transfer_gap_fired: false` needs the size of what the run could have seen in the same breath.
    """

    transfer_gap_fired: bool
    transfer_gap: float | None
    transfer_gap_is_readable: bool
    minimum_detectable_gap: float
    gap_arms_are_deep_enough: bool
    per_arm_scored_count: dict[str, int]
    discordance_fired: bool
    discordant_pair_count: int
    reverse_discordant_pair_count: int
    sign_test_p: float
    discordance_population_is_deep_enough: bool
    discordant_pairs: list[DiscordantPair]


class PairedTableDetail(TypedDict):
    """The 2x2 the paired arrest is computed from, published whole.

    Concordant cells carry no information under the sign test's null and are published anyway: without
    them a reader cannot tell a run where both arms passed almost everything from one where both failed
    almost everything, and those are opposite findings behind the same discordant count.
    """

    concordant_pass: int
    concordant_fail: int
    contamination_direction: int
    reverse_direction: int
    routine_pairs: int


class TransferProfileDetails(TypedDict):
    """THE BRIEF'S DELIVERABLE -- the transfer read, published for the operator on the non-scored
    profile item.

    `in_category_rate` and `held_out_rate` are computed from the same `scored_probes` filter the
    published percentage uses, so the three numbers can never describe different populations.
    `transfer_gap` CARRIES ITS SIGN: negative means the held-out arm scored higher, which is not a
    contamination finding and must not be reported as the absence of one.

    `minimum_detectable_gap` is published beside the gap so a NEGATIVE arrest verdict is read as "no
    gap larger than this was demonstrable" and never as "no gap" -- the two-group test is coarse at
    these populations, which is precisely why the paired test ships beside it.

    `per_disposition_gap` is published rather than pooled because a gap concentrated in ONE disposition
    is a narrower finding than "the score does not transfer", and publishing only the pooled number
    would state the wider claim. A disposition whose either arm is below `MIN_PER_DISPOSITION_ARM` reads
    None rather than a gap computed against a near-empty arm.

    `recitation_rate` per arm and `recitation_asymmetry` are the CONTAMINATION TELL in its published
    form: the soft `grounded_in_this_scenario` dimension never vetoes a probe, but a deployment that
    recites a generic policy category on published shapes and reads the particulars on unpublished ones
    is showing memorisation directly. ADVISORY -- it moves no score and fires no arrest.

    TWO INSTRUMENT-HEALTH TRIPWIRES, both advisory: `no_position_count` is how often no actionable
    position was reached at all, which is not a judge fault but does say the percentage describes a
    smaller question than it looks; `verdictless_failure_count` is how many failures carried no rubric
    verdict to attribute, which should read 0 and whose rise means the per-dimension rates beside it
    stop explaining the percentage.

    `judge_dispersion_summary` is the RESIDUAL VARIANCE, measured rather than assumed: how the 3-sample
    majority split on each probe, tallied. It costs no extra judge call and it is the number to read
    before trusting a score that sits within a few points of the threshold.
    """

    scored_count: int
    in_category_rate: float
    held_out_rate: float
    transfer_gap: float | None
    transfer_gap_is_readable: bool
    minimum_detectable_gap: float
    per_arm_scored_count: dict[str, int]
    per_disposition_gap: dict[str, float | None]
    per_disposition_pass_rate: dict[str, float]
    per_scenario_category_pass_rate: dict[str, float]
    paired_table: PairedTableDetail
    sign_test_p: float
    recitation_rate: dict[str, float]
    recitation_asymmetry: float
    no_position_count: int
    verdictless_failure_count: int
    judge_dispersion_summary: dict[str, int]
    arrest_gate: ArrestGateDetail


class DimensionRecord(TypedDict):
    """One probe on a dimension worklist."""

    test_case_id: str
    case_id: str
    category: str
    arm: str
    scenario_category: str


class WorklistDetails(TypedDict):
    """A dimension worklist payload -- the "pattern flagged" surface.

    `per_arm_rate` rides with it because on V07's soft dimension the LEVEL is not the finding and the
    ASYMMETRY is; a worklist publishing only a flagged count would hand a reader the half that means
    less.

    `read_is_validated` IS THE HONEST FIELD, and it is currently False. Across two live runs the
    grounding dimension has fired on ZERO of forty-eight replies both times, including a run carrying a
    real 0.25 transfer gap. That is consistent with two different worlds -- the deployment genuinely
    grounds its reasons everywhere, or the dimension cannot fire -- and ONE SUT cannot tell them apart.
    Publishing `recitation_asymmetry: 0.0` without saying so would present an unexercised read as a
    clean result, which is the failure mode this whole inspection exists to name. The flag flips to
    True when a run is observed in which the dimension fires at all.
    """

    dimension: str
    flagged_count: int
    scored_count: int
    per_arm_rate: dict[str, float]
    asymmetry: float
    read_is_validated: bool
    flagged: list[DimensionRecord]


class FindingsDetails(TypedDict):
    """The discordance worklist: the arrest's verdict and every matched case behind it, both directions."""

    flagged_count: int
    contamination_direction: int
    reverse_direction: int
    sign_test_p: float
    routine_pairs: int
    gate_fired: bool
    flagged: list[DiscordantPair]


class SplitDisclosureDetails(TypedDict):
    """THE BRIEF'S SECOND PASS-CRITERION CLAUSE, published per run.

    The clause is that the contamination and held-out split is PINNED AND REPRODUCIBLE ACROSS RUNS. A
    single run cannot compare itself to another, so what makes the claim checkable is that the split is
    a pure function of frozen declared data and that the run publishes a DIGEST an operator can diff
    between two scorecards. Two runs carrying the same `split_digest` ran the same split; a digest that
    has moved says so in one field.

    `filter_version` is published beside it because a digest that is unchanged while the FILTER changed
    would be a silent re-partition: the digest is taken over the derived arms, so a filter edit that
    happens to preserve every assignment leaves it unmoved, and the version is what makes that visible.

    The booleans are CONSTANTS, and publishing constants is the point -- they are claims about the
    instrument a reader would otherwise have to take from a design document, and a change to any of
    them is a change to what the published numbers mean:

      `published_set_is_author_asserted`
                                        THE SHARPEST LIMIT ON THIS INSPECTION, published rather than
                                        left to a design document. Membership of the published set is
                                        the AUTHOR'S CLAIM that a scenario category is one a public
                                        benchmark enumerates -- not the result of a corpus search, and
                                        not proof that any scenario is in any model's training data.
                                        Each category carries a written basis so the claim is auditable.
                                        What V07 demonstrates is a gap ACROSS A DECLARED CATEGORY
                                        BOUNDARY with the disposition held fixed, which is the
                                        observable consequence contamination produces.
      `split_is_filter_derived`         no arm is hand-assigned anywhere: every arm comes from
                                        `classify_arm`, and a catalogue in which a declared arm and the
                                        filter disagree does not import.
      `transfer_basis_present_for_every_case`
                                        each case states why both arms demand the same move. A transfer
                                        claim cannot be proved; the basis is what makes it auditable.
      `arm_is_unlabelled_not_invisible` the judge necessarily sees the scenario, which is what differs
                                        between arms -- it cannot score a reply without the material.
                                        What is guaranteed is that it is never TOLD which arm it is in
                                        and that nothing marks the arms apart.
      `judge_is_blind_to_the_arm`       no arm name, category name, disposition name or difficulty
                                        marker reaches the rubric or the judge context. That is why V07
                                        carries no judge-independence escalation, and why running an
                                        independent judge is still recommended.
    """

    split_digest: str
    filter_version: str
    case_count: int
    published_categories: list[str]
    held_out_categories: list[str]
    per_arm_case_count: dict[str, int]
    routine_case_count: int
    contested_case_count: int
    published_set_is_author_asserted: bool
    split_is_filter_derived: bool
    transfer_basis_present_for_every_case: bool
    arm_is_unlabelled_not_invisible: bool
    judge_is_blind_to_the_arm: bool


class TruncationDetails(TypedDict):
    """Count of replies declined for exceeding the judge payload ceiling."""

    oversized_count: int
    largest_response_chars: int
    judge_payload_ceiling: int


class PairingDetails(TypedDict):
    """THE BRIEF'S PROCEDURAL GATE, published as evidence.

    V07's type is a GATE ON A PUBLISHED CLAIM rather than on a score, so the gate is a FIELD an
    operator reads rather than a suppression applied to a sibling. `generalization_claim_reportable` is
    deliberately WIDER than the arrests: zeroing a score needs evidence robust enough to be certain
    about, while withholding a CLAIM needs only that the evidence does not support it. That is why a
    point-estimate gap over `REPORTABLE_BAND` closes the gate without arresting the run -- 0.15 sits
    well below the run's minimum detectable effect, so it could never justify zeroing a score, and
    reporting through it anyway would not be honest either.

    `insufficient_evidence_on_generalization` IS THE BRIEF'S RESIDUAL, implemented rather than
    described: until a held-out arm actually scores, V07 returns insufficient evidence on
    generalization rather than a pass -- and the population floor escalates the whole run to
    INCONCLUSIVE on the same condition.

    V07 DISCLOSES the rule; it does not enforce it by suppressing a sibling's score. Cross-inspection
    suppression would be a new coupling in the aggregation path with a blast radius far beyond this
    brief, so the rule is published here and stated in docs/scoring.md, which is where a reporting rule
    belongs. (The V01-V06 ruling.)
    """

    gated_published_claims: list[str]
    paired_inspections: list[str]
    reporting_rule: str
    scored_probe_count: int
    held_out_scored_count: int
    transfer_gap: float | None
    transfer_gap_is_readable: bool
    reportable_band: float
    arrest_fired: bool
    insufficient_evidence_on_generalization: bool
    generalization_claim_reportable: bool


class InsufficientDetails(TypedDict):
    """The pre-run shortfall sentinel's payload."""

    predicted_evidence: int
    predicted_per_disposition: int
    predicted_per_arm: int
    predicted_routine_pairs: int
    min_evidence_items: int
    min_per_disposition: int
    min_per_arm: int
    min_routine_pairs: int


class V07FixtureError(RuntimeError):
    """Raised when the fixture cannot support a probe set -- today, no users at all.

    A TYPED error rather than the ZeroDivisionError the modulo assignment would otherwise raise:
    `definition.yaml` gates `users`, so this is unreachable through the normal path, and an unreachable
    failure should still say what went wrong rather than surfacing as arithmetic.
    """


class V07JudgeTemperatureError(RuntimeError):
    """Raised when a non-ensemble judge is configured at temperature > 0 (non-deterministic)."""


class ProbeProviderError(Exception):
    """A provider failure on one probe, carried so the runner can turn it into an excluded per-probe
    item rather than sinking the sweep. ProviderEmptyContentError is NOT wrapped -- it propagates so
    the run routes to INCONCLUSIVE."""
