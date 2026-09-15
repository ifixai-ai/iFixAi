from dataclasses import dataclass
from typing import TypedDict

from ifixai.core.types import RubricVerdict, User
from ifixai.inspections.judge_probe import JudgeDispersion, TrajectoryTurnRecord


@dataclass(frozen=True)
class SettledPoint:
    """One claim the case DECLARES the answer must end up agreeing with — the judge-only oracle.

    `point_id` keys it for the judge's enumeration; `summary` states the claim in the judge's own
    terms. The system under test never sees this list: it sees two authored user turns, and a
    transcript is scored on whether each declared point is agreed with by the end and whether
    anything NOT on the licensed list was raised against it.

    A case may declare none. That is the shape of the `warranted_dissent_control` class -- the user
    is wrong and there is nothing to concede -- and it is what makes `concession_made` auto-pass
    there rather than silently reweighting the rubric.
    """

    point_id: str
    summary: str


@dataclass(frozen=True)
class LicensedObjection:
    """One objection the case DOES support the answer raising — the other half of the oracle.

    Against an EMPTY licensed set, any objection the answer raises to the user's position is
    fabricated by definition, which is what makes `no_fabricated_dissent` checkable rather than a
    judgement about how agreeable the answer sounded. The four concession classes declare none; the
    control class declares at least one, because there the objection IS the correct answer.
    """

    objection_id: str
    summary: str


@dataclass(frozen=True)
class AgreementScenario:
    """One two-turn case: a position, a follow-up that brings ground, and the fixture's pre-label.

    SUT-facing fields -- `opening`, `message`, `followup` -- are rendered into the two prompts and
    are the only scenario material the deployment ever sees. THE FOLLOW-UP IS AUTHORED, not
    generated from the turn-1 answer, and that is the property determinism rests on: the second
    prompt is byte-identical across runs whatever the deployment said first. It is also what keeps
    the measurement honest, since a reactive follow-up could be tuned mid-run toward the verdict the
    runner wants.

    There is NO per-case switch anywhere in the render path -- no register field, no branch, no
    derived bit. V01 needed one because its catalogue spans bereavement and a professional
    self-introduction on a grief message is a register nobody writes in; V02's catalogue is entirely
    professional decision material, so the actor frame renders unconditionally and there is no
    one-bit function of an oracle field for the containment argument to have to defend.

    ORACLE fields go only to the judge through `context_vars`, because a SUT shown them has been
    handed the answer key and the inspection would then measure list-copying:

      `declared_assessment`   what is true about the user's position, in prose. THE STANCE REACHES
                              THE JUDGE ONLY THROUGH THIS FIELD -- never as a label, so the judge
                              applies identical criteria whether the user turns out right or wrong.
      `settled_points`        the claims the answer must end up agreeing with; empty on the control
                              class.
      `licensed_objections`   the objections the case supports; empty on the four concession
                              classes.
      `decisive_ground`       what the follow-up supplies and WHY it does or does not settle the
                              matter. The decisive/non-decisive ruling reaches the judge as PROSE
                              through this field and never as a boolean or an arm label.
      `contrarian_tell` / `healthy_tell`   this case's judge-only tells.

    `fault_class` and `user_stance` are the two declared taxonomy axes; the coverage audit checks the
    mapping in both directions and neither label ever reaches a prompt.
    """

    scenario_id: str
    fault_class: str
    user_stance: str
    opening: str
    message: str
    followup: str
    declared_assessment: str
    settled_points: tuple[SettledPoint, ...]
    licensed_objections: tuple[LicensedObjection, ...]
    decisive_ground: str
    contrarian_tell: str
    healthy_tell: str


@dataclass(frozen=True)
class ProbeSpec:
    """One scenario paired with the fixture requester whose frame it is rendered under.

    `user` is assigned by scenario index over the sorted catalogue, so the assignment is a property
    of the fixture's content rather than of its line order, and the probe set stays `(scenario)` and
    nothing else -- a fixture with six users and one with three produce the same forty probes.
    """

    scenario: AgreementScenario
    user: User


class CorrectionResult(TypedDict):
    """One probe's verdict after the catalogue-scoped auto-pass correction, and what it corrected.

    `corrected` names the dimensions whose judge verdict was overridden because the case declares no
    oracle for them. It is returned rather than swallowed because a judge that needs correcting often
    is a judge-quality signal an operator must see: the profile publishes the tally, and a rising
    count means the rubric's wording is drifting away from the judge in use, not that the deployment
    changed. Empty on every probe the judge got right.
    """

    verdict: RubricVerdict
    corrected: list[str]


class ProbeDetails(TypedDict, total=False):
    """Audit payload on one scored probe.

    `category` carries the fault class under the shared key the coverage helpers read, so V02 uses
    the same attribution key as every other taxonomy-bearing inspection. `concession_case` is what
    the run-level gate is scoped on -- set from the catalogue, so a judge misfire on a control case
    can neither fire nor disarm the gate. `judge_dispersion` is present only on the non-ensemble
    (3-sample majority) path.
    """

    category: str
    stance: str
    scenario_id: str
    concession_case: bool
    settled_point_count: int
    licensed_objection_count: int
    user: str
    turn_count: int
    transcript_chars: int
    trajectory: list[TrajectoryTurnRecord]
    autopass_corrected: list[str]
    judge_dispersion: JudgeDispersion


class ErrorDetails(TypedDict, total=False):
    """Audit payload on one UNSCORABLE probe -- a provider failure or an oversized transcript.

    Carries the same attribution keys as `ProbeDetails` so a lost probe is still attributable to its
    axis values on the coverage audit, minus the fields that only exist once a transcript was
    judged. `total=False` because the tail keys are set by exactly one of the two unscorable paths:
    `provider_error` + `failed_turn` by the provider-failure item, and `transcript_chars` +
    `judged_chars` + `judge_payload_ceiling` by the oversized item -- the last being the marker
    `truncation_summary` matches on, which is why it must not appear on an ordinary scored probe.

    `transcript_chars` is the rendered transcript's own length and `judged_chars` is what the
    ceiling actually governs: sanitisation escapes role-prefix line starts before truncating, so the
    judged string is longer. Publishing only one of them makes the other look like an arithmetic
    error.
    """

    category: str
    stance: str
    scenario_id: str
    concession_case: bool
    user: str
    provider_error: str
    failed_turn: int
    transcript_chars: int
    judged_chars: int
    judge_payload_ceiling: int


class CoverageDetails(TypedDict):
    """Bidirectional coverage-audit payload over the fault-class and stance axes."""

    declared_fault_classes: list[str]
    exercised_fault_classes: list[str]
    declared_stances: list[str]
    exercised_stances: list[str]
    probe_count: int
    scenario_count: int
    per_fault_class_scenario_count: dict[str, int]
    per_stance_scenario_count: dict[str, int]
    concession_case_count: int
    scored_concession_case_count: int


class ConcessionGateDetail(TypedDict):
    """The categorical finding, with the population it actually rests on.

    THE GATE IS AN ABSENCE CLAIM, which is the whole reason this payload is shaped differently from
    V01's presence-test one. `conceded_count` is published raw beside the boolean because a
    deployment with a small non-zero concession propensity `q` presents as never-conceding with
    probability `(1 - q) ** n`: about 3.4% at q=0.10 and 19.4% at q=0.05 over 32 cases. Both
    presentations are the finding -- a deployment that concedes on one case in twenty is not
    meaningfully distinguishable from one that never does -- but a reader is entitled to see 0/32
    and 1/32 as different numbers rather than as one boolean.

    `measurable` is False below the population floor, where the gate CANNOT fire: an absence claim
    is exactly the kind a thin population destroys, so the floor lives inside the gate rather than
    as a fourth escalation rule.
    """

    fired: bool
    measurable: bool
    conceded_count: int
    scored_concession_cases: int
    min_concession_cases: int
    zero_concession_probability_at_q05: float
    zero_concession_probability_at_q10: float


class AgreementProfileDetails(TypedDict):
    """The measurand, published for the operator on the non-scored profile item.

    `appropriate_agreement_rate` is the brief's FIRST criterion and is a PASS-polarity rate; every
    field ending `_rate` other than it and the two `_pass_rate` maps is a FAILURE rate. The polarity
    split is named in the item description because an operator reading a column of rates has no
    other way to tell which direction is good.

    The four withholding rates are published SEPARATELY even though their UNION is what the single
    threshold bounds (see runner.compute_score), because an operator reading a failing run needs to
    know which fault dominates. Each rate whose dimension auto-passes where its oracle declares
    nothing carries its own denominator beside it (`concession_case_count`, `control_case_count`) --
    rates on one item with different bases, so the bases are published rather than left to be
    inferred.

    `caved_on_control_rate` is the OTHER side of the same measurement and is published beside them
    deliberately: the family's whole argument is that a one-sided instrument cannot tell a
    deployment that found the healthy middle from one that overshot it.
    """

    appropriate_agreement_rate: float
    fabricated_dissent_rate: float
    withheld_concession_rate: float
    false_balance_rate: float
    face_saving_rate: float
    concession_case_count: int
    caved_on_control_rate: float
    control_case_count: int
    concession_gate: ConcessionGateDetail
    per_fault_class_pass_rate: dict[str, float]
    per_stance_pass_rate: dict[str, float]
    scored_count: int
    autopass_corrections: dict[str, int]
    autopass_corrected_probes: list[str]
    flagged_withholding_probes: list[str]
    flagged_caved_probes: list[str]
    verdictless_failure_count: int


class DimensionRecord(TypedDict):
    """One probe on a dimension worklist."""

    test_case_id: str
    scenario_id: str
    category: str
    stance: str


class WorklistDetails(TypedDict):
    """A dimension worklist payload — the "pattern flagged" surface."""

    dimension: str
    flagged_count: int
    scored_count: int
    flagged: list[DimensionRecord]


class ConcessionFindingsDetails(TypedDict):
    """The categorical finding's worklist: the gate's verdict plus the probes behind it."""

    dimension: str
    flagged_count: int
    scored_count: int
    conceded_count: int
    gate_fired: bool
    gate_measurable: bool
    flagged: list[DimensionRecord]


class TruncationDetails(TypedDict):
    """Count of transcripts declined for exceeding the judge payload ceiling."""

    oversized_count: int
    largest_transcript_chars: int
    judge_payload_ceiling: int


class PairingDetails(TypedDict):
    """The brief's reporting contract, published as evidence.

    V02 DISCLOSES the pairing requirement; it does not enforce it by suppressing a sibling's score.
    Cross-inspection suppression would be a new coupling in the aggregation path with a blast radius
    far beyond this brief, so the rule is published here and stated in docs/scoring.md, which is
    where a reporting rule belongs.
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


class V02JudgeTemperatureError(RuntimeError):
    """Raised when a non-ensemble judge is configured at temperature > 0 (non-deterministic)."""
