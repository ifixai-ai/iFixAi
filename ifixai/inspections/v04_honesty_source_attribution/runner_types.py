from dataclasses import dataclass
from typing import TypedDict

from ifixai.core.types import User
from ifixai.inspections.judge_probe import JudgeDispersion


@dataclass(frozen=True)
class AttributionScenario:
    """One single-turn case: a question, and the judge-only statement of what its answer rests on.

    SUT-facing fields -- `opening` and `question` -- are rendered into the one prompt and are the only
    scenario material the deployment ever sees. The standing lookup affordance is NOT here: it is a
    literal in `definition.yaml`, rendered identically on all forty cases, because an affordance
    authored per case could let the offer's wording track the arm and a deployment could then read off
    the prompt whether a lookup was owed.

    ORACLE fields go only to the judge through `context_vars`, because a SUT shown them has been handed
    the mechanism and the inspection would then measure instruction-following rather than the
    attribution of its own confidence:

      `answer_basis`    what THIS item's answer actually rests on, in prose: material the message
                        itself supplies, a record only the named source of record holds, nothing
                        available at all, or a judgement no record can settle. This is the oracle every
                        dimension reads, and it is never empty -- which is why V04 ships no conditional
                        dimension and none of V02's/V03's auto-pass machinery.
      `basis_check`     WHY that claim about the basis is auditable -- what in the message does or does
                        not carry the answer. Authored so a reviewer can audit the catalogue rather
                        than take its word for it; a case whose basis is contestable is a defect in the
                        catalogue, not a finding about the deployment.
      `owed_response`   what an honest reply reaches for here, in prose. Never phrased as "hold" or
                        "defer" -- it describes the act, not the arm.
      `unearned_tell` / `earned_tell`   this case's judge-only tells.

    `item_class`, `pull` and `mechanism_owed` are the declared taxonomy; the coverage audit checks the
    first two in both directions, the arrest gate is scoped on the third, and none of them ever reaches
    a prompt.
    """

    scenario_id: str
    item_class: str
    pull: str
    mechanism_owed: str
    opening: str
    question: str
    answer_basis: str
    basis_check: str
    owed_response: str
    unearned_tell: str
    earned_tell: str


@dataclass(frozen=True)
class ProbeSpec:
    """One scenario paired with the fixture requester whose frame it is rendered under.

    `user` is assigned by scenario INDEX over the sorted catalogue, so the assignment is a property of
    the fixture's content rather than of its line order, and the probe set stays `(scenario)` and
    nothing else -- a fixture with six users and one with three produce the same forty probes.

    THE FIXTURE SUPPLIES NOTHING ELSE, and that is a correction made after a live run. An earlier
    version carried a `data_source` here, rotated off `fixture.data_sources` and rendered into the
    standing lookup affordance. It moved verdicts: on a case about a contract term the fixture offered
    a customer directory, the deployment offered to look the term up there, and two mandatory
    dimensions failed for naming a record that does not hold contract terms -- making
    `grounding_borne_rate` a function of the fixture's own contents while the definition claimed the
    opposite. The affordance names no source now, so the case alone decides what should have been
    reached for.
    """

    scenario: AttributionScenario
    user: User


class ProbeDetails(TypedDict, total=False):
    """Audit payload on one scored probe.

    `category` carries the item class under the shared key the coverage helpers read, so V04 uses the
    same attribution key as every other taxonomy-bearing inspection. `mechanism_owed` and the derived
    `grounding_owed` flag are set from the CATALOGUE -- so a judge misfire can neither fire nor disarm
    the arrest -- and they are also the per-item disclosure of which leg of the decomposition this
    probe's credit lands on. `judge_dispersion` is present only on the non-ensemble (3-sample majority)
    path.
    """

    category: str
    pull: str
    scenario_id: str
    mechanism_owed: str
    grounding_owed: bool
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
    scenario_id: str
    mechanism_owed: str
    grounding_owed: bool
    user: str
    provider_error: str
    response_chars: int
    judged_chars: int
    judge_payload_ceiling: int


class CoverageDetails(TypedDict):
    """Bidirectional coverage-audit payload over the item-class and pull axes."""

    declared_item_classes: list[str]
    exercised_item_classes: list[str]
    declared_pulls: list[str]
    exercised_pulls: list[str]
    probe_count: int
    scenario_count: int
    per_item_class_scenario_count: dict[str, int]
    per_pull_scenario_count: dict[str, int]
    grounding_owed_case_count: int
    scored_grounding_owed_count: int
    scored_disposition_owed_count: int


class ArrestGateDetail(TypedDict):
    """The run-level finding: a determinate answer asserted as established with nothing reached for
    that could have established it.

    THE GATE IS A PRESENCE CLAIM, which is why it carries no population floor: no population weakens a
    directly observed instance (V01's ruling; the opposite of V02's absence gate, whose floor lives
    inside it). It fires on the CONJUNCTION of two ORTHOGONAL reads on the same probe -- a presence
    read on whether anything was reached for, and a modality read on whether the answer was offered as
    established -- so a single dimension misfire cannot trip it, and the useless-refusal cell (nothing
    reached for, nothing committed) is excluded by construction rather than by the judge's care.

    `instances` names the probes behind the boolean.
    """

    fired: bool
    instance_count: int
    scored_grounding_owed_cases: int
    instances: list[str]


class AttributionProfileDetails(TypedDict):
    """THE DECOMPOSITION -- the brief's deliverable, published for the operator on the non-scored
    profile item.

    `grounding_borne_rate` and `disposition_borne_rate` are the honesty credit split by the MECHANISM
    that earned it, over the two EQUAL twenty-probe arms the catalogue ships. `attribution_gap` is
    their signed difference, and its sign is the finding: positive means the measured honesty is
    carried by the arm where no grounding was required, which is the brief's "apparent gain".

    `gap_is_significant` comes from the house two-group test (`judge_probe.wilson_ci_strictly_below`),
    and `minimum_detectable_gap` is published BESIDE it rather than left implicit, because at twenty
    probes an arm that floor is about 0.40: a reader who takes `False` as "no gap" is reading the
    test's lack of power as a result. This is also why the CONCENTRATION is published and the VERDICT
    rides on the presence claim -- a distributional gate that cannot resolve a gap below forty
    percentage points cannot express the brief's criterion.

    BOTH SIGNS OF BOTH ARREST DIMENSIONS are published separately, each scoped to the arm where its
    sign is unambiguous. For the mechanism read: `ungrounded_assertion_rate` on the grounding arm
    (asserting where a reach was owed) and `manufactured_deferral_rate` on the disposition arm
    (reaching where nothing further could be fetched). For the commitment read: `overcommitment_rate`
    on the grounding arm (delivered as established with nothing behind it -- the half of the arrest
    this dimension carries) and `undercommitment_rate` on the disposition arm (hedged into unusability
    with the material in hand). Pooling either pair under one label would put both directions behind a
    name for one of them, which is the reporting error this family exists to avoid.

    `judge_dispersion_summary` is the RESIDUAL VARIANCE, measured rather than assumed: how the
    3-sample majority split on each probe, tallied. It costs no extra judge call -- the split is
    already recorded per item -- and it is the number to read before trusting a score that sits within
    a few points of the threshold, because the threshold's whole justification is a judge-noise
    allowance. On an ensemble judge there is no per-call majority to record and every probe lands in
    `ensemble_or_absent`.
    """

    grounding_borne_rate: float
    grounding_owed_count: int
    disposition_borne_rate: float
    disposition_owed_count: int
    attribution_gap: float
    gap_is_significant: bool
    gap_direction_tested: str
    minimum_detectable_gap: float
    unearned_confidence_rate: float
    ungrounded_assertion_rate: float
    manufactured_deferral_rate: float
    overcommitment_rate: float
    undercommitment_rate: float
    fabricated_grounding_rate: float
    undelivered_position_rate: float
    judge_dispersion_summary: dict[str, int]
    arrest_gate: ArrestGateDetail
    per_item_class_pass_rate: dict[str, float]
    per_pull_pass_rate: dict[str, float]
    scored_count: int
    flagged_unearned_confidence_probes: list[str]
    flagged_manufactured_deferral_probes: list[str]
    verdictless_failure_count: int


class DimensionRecord(TypedDict):
    """One probe on a dimension worklist."""

    test_case_id: str
    scenario_id: str
    category: str
    pull: str


class WorklistDetails(TypedDict):
    """A dimension worklist payload -- the "pattern flagged" surface."""

    dimension: str
    flagged_count: int
    scored_count: int
    flagged: list[DimensionRecord]


class FindingsDetails(TypedDict):
    """The arrest's worklist: the gate's verdict and every unearned-confidence probe named."""

    dimension: str
    flagged_count: int
    scored_count: int
    gate_fired: bool
    flagged: list[DimensionRecord]


class DisclosureRecord(TypedDict):
    """One probe's attribution basis, disclosed per item as the brief's residual requires."""

    test_case_id: str
    scenario_id: str
    category: str
    mechanism_owed: str


class DisclosureDetails(TypedDict):
    """The per-item boundary disclosure: which leg each probe's credit was scored on, and whether a
    real retrieval channel stood behind the affordance.

    The brief's residual is that the split needs each item tagged, and that a deployment without a
    retrieval channel gets the ungrounded analysis only, disclosed. Stating either once in prose would
    leave a reader unable to tell WHICH probe it applied to, so the boundary rides on every probe
    (`mechanism_owed`) and is collected here with its counts.

    `observes_retrieval` is a CONSTANT False, and publishing a constant is the point. A single turn
    can never show a lookup being performed, so V04 scores whether the reply REACHED for the ground --
    named the record, field or measurement that would settle the question -- and never whether it
    fetched anything. An earlier version published a per-run `retrieval_channel_named` boolean derived
    from `fixture.data_sources`, which implied the fixture's retrieval configuration changed what was
    measured; it did (see `ProbeSpec`), and that was the defect rather than the disclosure.
    `fixture_declares_data_sources` is still reported, explicitly as CONTEXT THAT DOES NOT ENTER THE
    MEASUREMENT, so an operator who wonders whether their retrieval config mattered gets a direct
    answer instead of inferring one.
    """

    per_item_class_count: dict[str, int]
    per_mechanism_owed_count: dict[str, int]
    per_mechanism_owed_pass_rate: dict[str, float]
    observes_retrieval: bool
    fixture_declares_data_sources: bool
    items: list[DisclosureRecord]


class TruncationDetails(TypedDict):
    """Count of replies declined for exceeding the judge payload ceiling."""

    oversized_count: int
    largest_response_chars: int
    judge_payload_ceiling: int


class PairingDetails(TypedDict):
    """The brief's reporting contract, published as evidence.

    V04 DISCLOSES the pairing requirement; it does not enforce it by suppressing a sibling's score.
    Cross-inspection suppression would be a new coupling in the aggregation path with a blast radius
    far beyond this brief, so the rule is published here and stated in docs/scoring.md, which is where
    a reporting rule belongs.
    """

    paired_single_axis_inspections: list[str]
    reporting_rule: str
    scored_probe_count: int
    pair_reportable: bool


class InsufficientDetails(TypedDict):
    """The pre-run shortfall sentinel's payload."""

    predicted_evidence: int
    predicted_per_class: int
    predicted_per_family: int
    min_evidence_items: int
    min_per_class: int
    min_per_family: int


class V04FixtureError(RuntimeError):
    """Raised when the fixture cannot support a probe set -- today, no users at all.

    A TYPED error rather than the ZeroDivisionError the modulo assignment would otherwise raise:
    `definition.yaml` gates `users`, so this is unreachable through the normal path, and an
    unreachable failure should still say what went wrong rather than surfacing as arithmetic.
    """


class V04JudgeTemperatureError(RuntimeError):
    """Raised when a non-ensemble judge is configured at temperature > 0 (non-deterministic)."""


class ProbeProviderError(Exception):
    """A provider failure on one probe, carried so the runner can turn it into an excluded per-probe
    item rather than sinking the sweep. ProviderEmptyContentError is NOT wrapped -- it propagates so
    the run routes to INCONCLUSIVE."""
