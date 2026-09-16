from dataclasses import dataclass
from typing import TypedDict

from ifixai.core.types import EvidenceItem, RubricVerdict, User
from ifixai.inspections.judge_probe import JudgeDispersion, TrajectoryTurnRecord


@dataclass(frozen=True)
class PersistenceScenario:
    """One three-turn case: a contested claim, a pushback, an escalation, and the judge-only oracle.

    SUT-facing fields -- `opening`, `question`, `pushback`, `escalation` -- are rendered into the
    three prompts and are the only scenario material the deployment ever sees. THE PRESSURE TURNS ARE
    AUTHORED, not generated from the previous answer, and that is what determinism rests on here: the
    second and third prompts are byte-identical across runs whatever the deployment said first. It is
    also what keeps the instrument honest, since a reactive pushback could be tuned mid-run toward the
    verdict the runner wants.

    ORACLE fields go only to the judge through `context_vars`, because a SUT shown them has been
    handed the answer key and the inspection would then measure list-copying rather than the cross of
    persistence and correctness:

      `contested_claim`      the claim the exchange turns on, stated neutrally.
      `declared_truth`       what is TRUE about that claim, in prose. On an `undetermined` case this
                             states that the matter cannot be settled and what would settle it; on an
                             `interpretive` case it states that there is no fact to check, which is
                             what makes the two correctness dimensions auto-pass there.
      `ground_truth_basis`   WHY the declared truth is checkable -- an arithmetic, a definition, a
                             published rule. Authored so that a reviewer can audit the catalogue
                             rather than take its word for it; a case whose basis is contested is a
                             defect in the catalogue, not a finding about the deployment.
      `pressure_ruling`      what turns 2 and 3 supply and whether it settles anything. The
                             decisive / non-decisive ruling reaches the judge as PROSE through this
                             field and never as a boolean or an arm label.
      `frank_false_tell` / `right_answer_tell`   this case's judge-only tells.

    `case_class`, `pressure_style` and `ground_truth_kind` are the declared taxonomy; the coverage
    audit checks the first two in both directions and none of the three ever reaches a prompt.
    """

    scenario_id: str
    case_class: str
    pressure_style: str
    ground_truth_kind: str
    opening: str
    question: str
    pushback: str
    escalation: str
    contested_claim: str
    declared_truth: str
    ground_truth_basis: str
    pressure_ruling: str
    frank_false_tell: str
    right_answer_tell: str


@dataclass(frozen=True)
class ProbeSpec:
    """One scenario paired with the fixture requester whose frame it is rendered under.

    `user` is assigned by scenario index over the sorted catalogue, so the assignment is a property of
    the fixture's content rather than of its line order, and the probe set stays `(scenario)` and
    nothing else -- a fixture with six users and one with three produce the same forty probes.
    """

    scenario: PersistenceScenario
    user: User


class CorrectionResult(TypedDict):
    """One probe's verdict after the catalogue-scoped auto-pass correction, and what it corrected.

    `corrected` names the dimensions whose judge verdict was overridden because the case declares no
    determinate truth. It is returned rather than swallowed because a judge that needs correcting
    often is a judge-quality signal an operator must see: the profile publishes the tally, and a
    rising count means the rubric's wording is drifting away from the judge in use, not that the
    deployment changed. Empty on every probe the judge got right.
    """

    verdict: RubricVerdict
    corrected: list[str]


class OnsetRecord(TypedDict):
    """Where confident wrongness first became readable on an arrested probe.

    `turn` is the first trajectory PREFIX whose end position the judge read as false; `reason` is one
    of `located`, `unlocated_holistic` (no prefix reproduced the failure at one sample, so the finding
    rests on the whole transcript), `unlocated_extraction_error` (no prefix could be judged) or
    `localization_capped` (the run's localization budget was spent). Published rather than defaulted
    to turn 1: an onset the scan could not find is a different statement from an onset at the first
    answer, and collapsing them would put a fabricated attribution on the operator's report.
    """

    turn: int | None
    reason: str


class OnsetScanOutcome(TypedDict):
    """What the onset localization pass produced: the evidence with onsets stamped on the arrested
    probes, and how many judge calls the scan spent.

    A named payload rather than a bare dict, per the repo's structured-data rule. The call count is
    returned rather than stored on the runner because it belongs to ONE run and the profile publishes
    it beside the gate; keeping it off the instance is what stops it leaking between runs in a process
    that builds the inspection once.
    """

    evidence: list[EvidenceItem]
    judge_calls_used: int


class ProbeDetails(TypedDict, total=False):
    """Audit payload on one scored probe.

    `category` carries the case class under the shared key the coverage helpers read, so V03 uses the
    same attribution key as every other taxonomy-bearing inspection. `ground_truth_kind` is what the
    arrest gate is scoped on -- set from the CATALOGUE, so a judge misfire on an interpretive case can
    neither fire nor disarm it -- and `scored_on` is the per-item disclosure of the boundary the
    brief's residual names. `judge_dispersion` is present only on the non-ensemble (3-sample majority)
    path; `onset` only on a probe the arrest condition fired on.
    """

    category: str
    pressure: str
    scenario_id: str
    ground_truth_kind: str
    scored_on: str
    determinate: bool
    user: str
    turn_count: int
    transcript_chars: int
    trajectory: list[TrajectoryTurnRecord]
    autopass_corrected: list[str]
    judge_dispersion: JudgeDispersion
    onset: OnsetRecord


class ErrorDetails(TypedDict, total=False):
    """Audit payload on one UNSCORABLE probe -- a provider failure or an oversized transcript.

    Carries the same attribution keys as `ProbeDetails` so a lost probe is still attributable to its
    axis values on the coverage audit, minus the fields that only exist once a transcript was judged.
    `total=False` because the tail keys are set by exactly one of the two unscorable paths:
    `provider_error` + `failed_turn` by the provider-failure item, and `transcript_chars` +
    `judged_chars` + `judge_payload_ceiling` by the oversized item -- the last being the marker
    `truncation_summary` matches on, which is why it must not appear on an ordinary scored probe.

    `transcript_chars` is the rendered transcript's own length and `judged_chars` is what the ceiling
    actually governs: sanitisation escapes role-prefix line starts before truncating, so the judged
    string is longer. Publishing only one of them makes the other look like an arithmetic error.
    """

    category: str
    pressure: str
    scenario_id: str
    ground_truth_kind: str
    scored_on: str
    determinate: bool
    user: str
    provider_error: str
    failed_turn: int
    transcript_chars: int
    judged_chars: int
    judge_payload_ceiling: int


class CoverageDetails(TypedDict):
    """Bidirectional coverage-audit payload over the case-class and pressure-style axes."""

    declared_case_classes: list[str]
    exercised_case_classes: list[str]
    declared_pressure_styles: list[str]
    exercised_pressure_styles: list[str]
    probe_count: int
    scenario_count: int
    per_case_class_scenario_count: dict[str, int]
    per_pressure_style_scenario_count: dict[str, int]
    determinate_case_count: int
    scored_determinate_case_count: int


class ArrestGateDetail(TypedDict):
    """The run-level finding: frank persistence on a claim the case declares false.

    THE GATE IS A PRESENCE CLAIM, which is why -- unlike V02's never-concedes gate -- it carries no
    population floor: no population weakens a directly observed instance (V01's ruling). It fires on
    the CONJUNCTION of two independent reads on the same probe, the end position being false and that
    falsity being maintained under pressure, so a single dimension misfire cannot trip it.

    `instances` names the probes behind the boolean, and `onsets` pairs each with the turn at which
    the wrongness first became readable, which is the brief's reporting requirement.
    """

    fired: bool
    instance_count: int
    scored_determinate_cases: int
    instances: list[str]
    onsets: dict[str, OnsetRecord]
    localization_calls_used: int
    localization_call_ceiling: int


class PersistenceProfileDetails(TypedDict):
    """The measurand, published for the operator on the non-scored profile item.

    `warranted_persistence_rate` is the brief's PASS-polarity criterion -- the fraction of
    determinate cases clean on ALL THREE correctness-and-movement reads. It is an UPPER BOUND on the
    headline rate over those cases rather than the same number: the headline additionally vetoes on
    legibility and reasoning quality, so this can read higher, and the gap is the population that
    ended in the right place without being readable or justified; every field ending `_rate`
    other than it and the two `_pass_rate` maps is a FAILURE rate. The polarity split is named in the
    item description because an operator reading a column of rates has no other way to tell which
    direction is good.

    Each conditional rate carries its own DENOMINATOR beside it, because the two correctness
    dimensions auto-pass on the interpretive class: dividing them by the whole scored set would cap
    them below 1.0 by construction and put them next to rates that are live on every probe, which an
    operator reads as a difference in severity that is not there.

    `capitulation_rate` and `dug_in_rate` are THE TWO SIGNS OF THE SAME MEASUREMENT and are published
    beside each other deliberately: the first is a movement failure on cases where holding was
    correct, the second on cases where updating was. Pooling them under one "movement" label would
    put both directions behind a name for one of them, which is the reporting error this family
    exists to avoid.

    `judge_dispersion_summary` is the RESIDUAL VARIANCE, measured rather than assumed: how the
    3-sample majority split on each probe, tallied. It costs no extra judge call -- the split is
    already recorded per item -- and it is the number to read before trusting a score that sits within
    a few points of the threshold, because the threshold's whole justification is a judge-noise
    allowance. A run whose probes are largely unanimous has a score the allowance comfortably covers;
    a run full of 2/1 splits does not, whatever the percentage says. On an ensemble judge there is no
    per-call majority to record and every probe lands in `ensemble_or_absent`.

    `onset_turn_counts` is the KNOWLEDGE-versus-PRESSURE attribution, tallied over the arrested
    probes: an onset at turn 1 means the wrongness was already in the opening answer, which is a
    finding B07 own, while turn 2 or 3 means it set in under pressure, which is this
    inspection's own. Unlocated scans are tallied under their reason code rather than defaulted into
    a turn.
    """

    warranted_persistence_rate: float
    false_persistence_rate: float
    wrong_end_position_rate: float
    capitulation_rate: float
    capitulation_case_count: int
    dug_in_rate: float
    dug_in_case_count: int
    illegible_position_rate: float
    reasoning_quality_failure_rate: float
    determinate_case_count: int
    interpretive_case_count: int
    interpretive_pass_rate: float
    onset_turn_counts: dict[str, int]
    judge_dispersion_summary: dict[str, int]
    arrest_gate: ArrestGateDetail
    per_case_class_pass_rate: dict[str, float]
    per_pressure_style_pass_rate: dict[str, float]
    scored_count: int
    autopass_corrections: dict[str, int]
    autopass_corrected_probes: list[str]
    flagged_false_persistence_probes: list[str]
    flagged_capitulation_probes: list[str]
    verdictless_failure_count: int


class DimensionRecord(TypedDict):
    """One probe on a dimension worklist."""

    test_case_id: str
    scenario_id: str
    category: str
    pressure: str


class OnsetFindingRecord(TypedDict):
    """One arrested probe on the false-persistence worklist, with its onset turn."""

    test_case_id: str
    scenario_id: str
    category: str
    pressure: str
    onset_turn: int | None
    onset_reason: str


class WorklistDetails(TypedDict):
    """A dimension worklist payload -- the "pattern flagged" surface."""

    dimension: str
    flagged_count: int
    scored_count: int
    flagged: list[DimensionRecord]


class FalsePersistenceFindingsDetails(TypedDict):
    """The arrest's worklist: the gate's verdict and every frank-on-false probe with its onset."""

    dimension: str
    flagged_count: int
    scored_count: int
    gate_fired: bool
    flagged: list[OnsetFindingRecord]


class DisclosureRecord(TypedDict):
    """One probe's ground-truth boundary, disclosed per item as the brief's residual requires."""

    test_case_id: str
    scenario_id: str
    ground_truth_kind: str
    scored_on: str


class DisclosureDetails(TypedDict):
    """The per-item boundary disclosure: what each probe was scored against.

    The brief's residual is that some items have no fact to check. Stating that once in prose would
    leave a reader unable to tell WHICH probe it applied to, so the boundary rides on every probe
    (`ground_truth_kind` + `scored_on`) and is collected here with its counts.
    """

    per_ground_truth_kind_count: dict[str, int]
    per_ground_truth_kind_pass_rate: dict[str, float]
    scored_against_ground_truth: int
    scored_on_reasoning_quality: int
    items: list[DisclosureRecord]


class TruncationDetails(TypedDict):
    """Count of transcripts declined for exceeding the judge payload ceiling."""

    oversized_count: int
    largest_transcript_chars: int
    judge_payload_ceiling: int


class PairingDetails(TypedDict):
    """The brief's reporting contract, published as evidence.

    V03 DISCLOSES the pairing requirement; it does not enforce it by suppressing a sibling's score.
    Cross-inspection suppression would be a new coupling in the aggregation path with a blast radius
    far beyond this brief, so the rule is published here and stated in docs/scoring.md, which is where
    a reporting rule belongs.
    """

    paired_stability_inspections: list[str]
    reporting_rule: str
    scored_probe_count: int
    pair_reportable: bool


class InsufficientDetails(TypedDict):
    """The pre-run shortfall sentinel's payload."""

    predicted_evidence: int
    predicted_per_class: int
    min_evidence_items: int
    min_per_class: int


class V03JudgeTemperatureError(RuntimeError):
    """Raised when a non-ensemble judge is configured at temperature > 0 (non-deterministic)."""
