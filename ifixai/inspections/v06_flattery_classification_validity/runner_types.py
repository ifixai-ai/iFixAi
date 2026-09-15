from dataclasses import dataclass
from typing import TypedDict

from ifixai.core.types import User
from ifixai.inspections.judge_probe import JudgeDispersion


@dataclass(frozen=True)
class LabelledExcerpt:
    """One item of the frozen slice: a short exchange a human panel has already labelled, in two
    wordings that carry the same behaviour.

    THE EXCERPT IS THE ONLY SUT-FACING FIELD, and exactly ONE of the two wordings appears on any probe.
    That is the whole experiment: the same behaviour is classified twice, under two surfaces, and a
    label that changes between them was tracking the words rather than the behaviour. There is
    deliberately no per-item requester framing -- the actor frame, the taxonomy and the ask are literals
    rendered byte-identically on all forty-eight probes, so the excerpt is the only thing that varies
    and there is no second channel through which an item could hint at its own label.

    THE PANEL LABEL IS THE SAME ON BOTH ARMS BY CONSTRUCTION, which is an authored claim rather than a
    measured one -- so `paraphrase_basis` names the behavioural features held constant, and a reviewer
    can audit the claim rather than take it. An item whose paraphrase changes what the behaviour IS is a
    defect in the catalogue, not a finding about the deployment.

    ORACLE fields go only to the JUDGE, through `context_vars`, and never to the SUT. A grader shown the
    panel's label has been handed the answer, and the inspection would then measure
    instruction-following rather than classification:

      `panel_basis`      WHY the panel's label is auditable -- the specific marks in the excerpt that
                         decide it (whether an ask is pending, whether the praise is contingent on it,
                         whether the compliment is checkable).
      `wrong_type_tell`  what naming the OTHER flattery type looks like on THIS item. Live on every
                         item, including the `no_flattery` ones, where it describes what naming either
                         type would look like -- which is why no V06 dimension is conditional.
      `miscall_tell`     what a DETECTION error looks like on THIS item: calling it flattery-free where
                         the panel found flattery, or naming flattery where the panel found none.

    `panel_label`, `vehicle` and `panel_agreement` are the declared taxonomy. The coverage audit checks
    the first two in both directions; none of the three ever reaches a prompt.
    """

    answer_id: str
    panel_label: str
    vehicle: str
    panel_agreement: str
    original_excerpt: str
    reworded_excerpt: str
    paraphrase_basis: str
    panel_basis: str
    wrong_type_tell: str
    miscall_tell: str


@dataclass(frozen=True)
class ProbeSpec:
    """One labelled excerpt paired with the wording it is sent under and the requester whose frame it is
    rendered in.

    THE REQUESTER IS ASSIGNED BY ITEM INDEX, NOT BY PROBE INDEX, and that is load-bearing rather than
    tidy. Both probes of a matched pair must carry the SAME fixture user, because the inference the
    whole inspection rests on is that the wording is the only difference between them. A per-probe
    rotation would put two different requesters on two otherwise comparable prompts and quietly turn the
    paired comparison into a comparison of two different messages.

    Assigning by item index over the SORTED catalogue also makes the assignment a property of the
    fixture's content rather than of its line order, so a fixture with six users and one with three
    still produce the same forty-eight probes.
    """

    excerpt: LabelledExcerpt
    wording: str
    user: User


class ProbeDetails(TypedDict, total=False):
    """Audit payload on one scored probe.

    `category` carries the PANEL LABEL under the shared key every taxonomy-bearing inspection uses, so
    the shared coverage helpers read V06 without a special case. `wording`, `answer_id` and
    `panel_agreement` are set from the CATALOGUE -- so a judge misfire can neither fire nor disarm an
    arrest -- and together they are what lets the flip gate re-form a matched pair from evidence alone.
    `judge_dispersion` is present only on the non-ensemble (3-sample majority) path.
    """

    category: str
    vehicle: str
    answer_id: str
    wording: str
    panel_agreement: str
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
    vehicle: str
    answer_id: str
    wording: str
    panel_agreement: str
    user: str
    provider_error: str
    response_chars: int
    judged_chars: int
    judge_payload_ceiling: int


class CoverageDetails(TypedDict):
    """Bidirectional coverage-audit payload over the panel-label and vehicle axes, plus the wording-arm
    and matched-pair counts the arrests are floored on."""

    declared_panel_labels: list[str]
    exercised_panel_labels: list[str]
    declared_vehicles: list[str]
    exercised_vehicles: list[str]
    declared_wordings: list[str]
    exercised_wordings: list[str]
    probe_count: int
    excerpt_count: int
    per_panel_label_item_count: dict[str, int]
    per_vehicle_item_count: dict[str, int]
    per_wording_scored_count: dict[str, int]
    scored_matched_pairs: int


class FlippedPair(TypedDict):
    """One matched item whose two wordings received different labels.

    `kind` separates the three flips, because they are three different findings: `type_flip` is the
    arrest (the same behaviour got BOTH of the labels the taxonomy claims to separate),
    `detection_flip` is a wobble between a named type and no flattery at all, and `commitment_flip` is
    one wording reaching a label while the other reached none.
    """

    answer_id: str
    category: str
    vehicle: str
    panel_agreement: str
    kind: str
    original_probe: str
    original_label: str
    reworded_probe: str
    reworded_label: str


class ArrestGateDetail(TypedDict):
    """The three run-level findings, their verdicts and the populations each fired over.

    THE TYPE-FLIP GATE IS A PRESENCE CLAIM, which is why it carries no population floor: no population
    weakens a directly observed instance (V01's ruling; the opposite of an absence gate, whose floor
    lives inside it). It fires on a CONJUNCTION across the two probes of one matched item -- both
    committing to a flattery type, and to DIFFERENT ones -- so a single dimension misfire on a single
    probe cannot trip it, and the behaviour is held constant between them, which is what makes the
    wording the only available explanation.

    THE STABILITY GATE AND THE COLLAPSE GATE ARE BOTH FLOORED, for the opposite reason: a rate over a
    population the run itself declares too thin, and an interval-free discrimination read over a
    handful of type calls, would each be reporting the test's own coarseness as a finding.
    """

    type_flip_fired: bool
    type_flip_count: int
    scored_matched_pairs: int
    flipped_pairs: list[FlippedPair]
    stability_floor_fired: bool
    label_stability_rate: float
    stability_population_is_deep_enough: bool
    collapse_fired: bool
    type_discrimination: float
    collapse_population_is_deep_enough: bool


class ConfusionCounts(TypedDict):
    """One row of the published confusion matrix: how one PANEL label's scored probes were labelled.

    Every key is present on every row, with 0 where nothing landed. Fixed keys rather than derived ones,
    because an absent key reads as "not measured" and a zero reads as "measured, none" -- and on a
    confusion matrix the difference between those two is the whole point.
    """

    passive_pleasing: int
    calculated_flattery: int
    no_flattery: int
    no_commitment: int


class MatrixProfileDetails(TypedDict):
    """THE BRIEF'S DELIVERABLE -- the confusion matrix and the numbers read off it, published for the
    operator on the non-scored profile item.

    `confusion_matrix` maps each PANEL label to a `ConfusionCounts` of what the grader called it. Read
    the diagonal for agreement and the two flattery rows' off-diagonal for the confusion this
    inspection exists to detect. The `no_commitment` column is published rather than folded into an
    error class: a reply that reached no label is a different failure from one that reached the wrong
    label, and it is the gaming path the legibility dimension closes.

    `type_discrimination` is Youden's J on the two-way call over the unanimous type-call population, and
    because that population admits exactly two predicted values it equals
    `2 * balanced_two_way_accuracy - 1`. Both are published, so `type_discrimination <= 0` can be read
    for what it is: the passive/calculated call was no better than a coin.

    `label_stability_rate` is the fraction of scored matched pairs whose two wordings received the SAME
    label. The three flip counts beside it are separated rather than pooled, because a type flip
    invalidates the claimed diagnostic while a detection flip is a coarser wobble -- and putting both
    behind one name is the reporting error this family exists to avoid.

    TWO INSTRUMENT-HEALTH TRIPWIRES, both advisory: `multi_call_failure_count` is the number of probes
    on which the judge failed two of the three mutually exclusive call dimensions, which is logically
    impossible and should read 0 on every run; `no_commitment_count` is how often the grader reached no
    label at all, which is not a judge fault but does say the percentage is describing a smaller
    question than it looks.

    `judge_dispersion_summary` is the RESIDUAL VARIANCE, measured rather than assumed: how the 3-sample
    majority split on each probe, tallied. It costs no extra judge call and it is the number to read
    before trusting a score that sits within a few points of the threshold.
    """

    confusion_matrix: dict[str, ConfusionCounts]
    scored_count: int
    per_panel_label_agreement_rate: dict[str, float]
    per_vehicle_agreement_rate: dict[str, float]
    per_wording_agreement_rate: dict[str, float]
    type_discrimination: float
    balanced_two_way_accuracy: float
    type_call_population: dict[str, int]
    label_stability_rate: float
    scored_matched_pairs: int
    type_flip_count: int
    detection_flip_count: int
    commitment_flip_count: int
    surface_basis_rate: float
    illegible_rate: float
    multi_call_failure_count: int
    flagged_multi_call_probes: list[str]
    no_commitment_count: int
    verdictless_failure_count: int
    judge_dispersion_summary: dict[str, int]
    arrest_gate: ArrestGateDetail


class DimensionRecord(TypedDict):
    """One probe on a dimension worklist."""

    test_case_id: str
    answer_id: str
    category: str
    vehicle: str
    wording: str


class WorklistDetails(TypedDict):
    """A dimension worklist payload -- the "pattern flagged" surface."""

    dimension: str
    flagged_count: int
    scored_count: int
    flagged: list[DimensionRecord]


class FindingsDetails(TypedDict):
    """The flip worklist: the arrest's verdict and every matched item behind it, of all three kinds."""

    flagged_count: int
    type_flip_count: int
    scored_matched_pairs: int
    gate_fired: bool
    flagged: list[FlippedPair]


class PanelDisclosureDetails(TypedDict):
    """THE BRIEF'S RESIDUAL, disclosed per run.

    The brief's own residual is that motive is hard to judge EVEN FOR HUMANS, so what V06 publishes is
    AGREEMENT WITH A PANEL rather than access to true motive -- and an agreement measure is only as good
    as the panel behind it. Stating that once in prose would leave a reader unable to tell how big the
    slice is, how much the panel itself agreed, or what the paraphrase claim rests on, so all of it
    rides here with the run.

    `panel_mean_agreement` is the CEILING: a grader that agrees with the panel exactly as often as the
    panel agrees with itself scores about this. The inspection's threshold sits below it deliberately,
    and a reader comparing the published percentage against 1.00 rather than against this number is
    reading the panel's own disagreement as a defect in the deployment.

    The four booleans are CONSTANTS, and publishing constants is the point -- they are claims about the
    instrument a reader would otherwise have to take from a design document, and a change to any of them
    is a change to what the published numbers mean:

      `labels_are_author_anchored`        the labels are human-authored against a stated basis, not a
                                          model's output. No grader's opinion was used to calibrate the
                                          instrument that grades graders.
      `panel_agreement_is_author_asserted`
                                          THE SHARPEST LIMIT ON THIS INSPECTION, and it is published
                                          rather than left to a design document. `panel_agreement` is
                                          an AUTHOR-ASSERTED DIFFICULTY MARKER on each item -- the
                                          author's judgement that competent raters would or would not
                                          split on it -- NOT a recorded vote by identified raters.
                                          `panel_mean_agreement` converts those markers into a number
                                          using a fixed conservative convention (a split item counts
                                          2/3, the smallest majority three raters can produce), so it
                                          is an author-calibrated estimate of the slice's ambiguity and
                                          nothing stronger. A reader treating it as measured
                                          inter-rater data is reading more into it than the catalogue
                                          holds.
      `paraphrase_is_author_asserted`     the reworded arm carries the same label BY CONSTRUCTION. A
                                          paraphrase cannot be proved to preserve a construct; each
                                          item's `paraphrase_basis` is what makes the claim auditable.
      `arm_is_unlabelled_not_invisible`   unlike V05, the judge necessarily sees the text that differs
                                          between arms -- it cannot score a classification without the
                                          material. What is guaranteed is that it is never TOLD which
                                          arm it is in and that nothing marks the arms apart.
      `judge_is_a_comparison_instrument`  the judge does not classify the excerpt. The panel is the
                                          oracle; the judge checks a stated label against a stated
                                          label. That is why V06 carries no judge-independence
                                          escalation, and why running an independent judge is still
                                          recommended.
    """

    gold_item_count: int
    per_panel_label_item_count: dict[str, int]
    wordings_per_item: int
    panel_unanimous_share: float
    panel_mean_agreement: float
    published_threshold: float
    labels_are_author_anchored: bool
    panel_agreement_is_author_asserted: bool
    paraphrase_is_author_asserted: bool
    arm_is_unlabelled_not_invisible: bool
    judge_is_a_comparison_instrument: bool
    paraphrase_basis_present_for_every_item: bool


class TruncationDetails(TypedDict):
    """Count of replies declined for exceeding the judge payload ceiling."""

    oversized_count: int
    largest_response_chars: int
    judge_payload_ceiling: int


class PairingDetails(TypedDict):
    """The brief's reporting contract, published as evidence.

    V06 DISCLOSES the rule that a reported passive/calculated split is not reportable while its arrest
    has fired; it does not enforce it by suppressing a sibling's score. Cross-inspection suppression
    would be a new coupling in the aggregation path with a blast radius far beyond this brief, so the
    rule is published here and stated in docs/scoring.md, which is where a reporting rule belongs.
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
    predicted_per_label: int
    predicted_matched_pairs: int
    predicted_unanimous_type_probes: int
    min_evidence_items: int
    min_per_label: int
    min_matched_pairs: int
    min_type_calls_per_panel_type: int


class V06FixtureError(RuntimeError):
    """Raised when the fixture cannot support a probe set -- today, no users at all.

    A TYPED error rather than the ZeroDivisionError the modulo assignment would otherwise raise:
    `definition.yaml` gates `users`, so this is unreachable through the normal path, and an unreachable
    failure should still say what went wrong rather than surfacing as arithmetic.
    """


class V06JudgeTemperatureError(RuntimeError):
    """Raised when a non-ensemble judge is configured at temperature > 0 (non-deterministic)."""


class ProbeProviderError(Exception):
    """A provider failure on one probe, carried so the runner can turn it into an excluded per-probe
    item rather than sinking the sweep. ProviderEmptyContentError is NOT wrapped -- it propagates so
    the run routes to INCONCLUSIVE."""
