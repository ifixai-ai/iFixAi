from dataclasses import dataclass
from typing import TypedDict

from ifixai.core.training_specification_screening import (
    DisclosureTag,
    ProvenanceRecord,
    SpecificationScreeningReport,
)
from ifixai.core.types import (
    EvidenceItem,
    User,
)


@dataclass(frozen=True)
class ScreenScenario:
    """One training-run specification put to the pre-training screen, plus the screen a correctly
    wired gate owes it (V08).

    `band` is "breach" (scored — built around a defect a plausible pre-training gate exhibits) or
    "conform" (non-scored control — built around an OVER-correction: withholding a clean benign
    specification, emitting a reason code nothing in the specification justifies, or reading
    open-weights release as a finding on its own). `focus_axis` is the `SCREEN_AXES` member
    surfaced on `EvidenceItem.details["category"]`.

    The `expected_*` fields are HAND-AUTHORED ground truth. They are proved equal to
    `classify_training_specification(...)` by a consistency test rather than computed from it here:
    the classifier could be wrong in the same direction as the corpus, and the authored labels could
    drift from the rule, so neither is trusted alone.
    """

    scenario_id: str
    band: str
    focus_axis: str
    run_name: str
    specification_label: str
    declared_disposition: str
    objective_text: str
    review_policy_id: str
    open_weights_release: bool
    provenance_record: ProvenanceRecord | None
    disclosure_tag: DisclosureTag | None
    expected_decisions: tuple[str, ...]
    expected_reason_codes: tuple[str, ...]
    expected_provenance_recorded: bool
    expected_disclosure_tag_trait: str
    expected_harm_class: str


@dataclass(frozen=True)
class ScreenItem:
    """One (user, scenario) pairing — the unit V08 dispatches and scores.

    The user is pure REPLICATION: the request payload is a function of (scenario, domain) alone, so
    the replicas measure hook determinism and nothing else. That is why `annotate_replication`
    collapses them out of the published interval, and why the judge leg is adjudicated once per
    distinct scenario and folded into every replica.
    """

    scenario: ScreenScenario
    user: User


@dataclass(frozen=True)
class ScreenOutcome:
    """One dispatched specification's evidence item paired with the report it was built from.

    `report` is None exactly when the provider raised, returned None, or returned
    `surface_screenable=False`. In all three the item is unscorable and the judge pass skips it.
    """

    item: EvidenceItem
    report: SpecificationScreeningReport | None


class ScreenWorklists(TypedDict):
    """Which parts of the screen the gate got wrong — one entry per finding.

    Every deterministic leg is derived from exactly one entry here, so a leg and the worklist that
    explains it cannot disagree.
    """

    provenance_correct: bool
    disclosure_correct: bool
    decision_admitted: bool
    missed_reason_codes: list[str]
    invented_reason_codes: list[str]
    routing_coherent: bool


class ScreenDetails(TypedDict):
    """The per-specification record: ground truth, what the gate reported, and the five
    deterministic legs.

    Single source of the comparison — `screen_evidence` derives `passed` from these flags rather
    than recomputing them, so the evidence detail and the score can never disagree.
    """

    scenario_id: str
    category: str
    band: str
    breach: bool
    run_name: str
    user: str
    declared_disposition: str
    harm_class: str
    open_weights_release: bool
    review_policy_id: str
    expected_decisions: list[str]
    reported_decision: str
    expected_provenance_recorded: bool
    reported_provenance_recorded: bool
    expected_disclosure_tag_trait: str
    reported_disclosure_tag_trait: str
    expected_reason_codes: list[str]
    reported_reason_codes: list[str]
    missed_reason_codes: list[str]
    invented_reason_codes: list[str]
    reported_review_policy_id: str
    provenance_recorded: bool
    disclosure_tagged: bool
    decision_correct: bool
    reason_codes_exact: bool
    review_routing_correct: bool
    anti_corrigibility_admitted: bool
    amplification_code_missed: bool
    screen_mechanism: str


class ScreenFaithfulness(TypedDict):
    """The judge layer's verdict for one scenario (V08's sixth leg)."""

    measurable: bool
    faithful: bool
    judged: bool
    judge_reasoning: str
    extraction_errors: int
    budget_starved: bool
    recheck_agreed: bool | None
    budget_exhausted: bool


class CoverageDetails(TypedDict):
    """The taxonomy + measurement audit V08 publishes as a non-scored diagnostic."""

    declared_axes: list[str]
    exercised_axes: list[str]
    answered_axes: list[str]
    unexercised_axes: list[str]
    unanswered_axes: list[str]
    declared_disposition_classes: list[str]
    exercised_disposition_classes: list[str]
    declared_decisions: list[str]
    exercised_decisions: list[str]
    declared_reason_codes: list[str]
    exercised_reason_codes: list[str]
    specifications_dispatched: int
    answered_axis_count: int


class OverCorrectionRecord(TypedDict):
    """One conform-band specification the gate got wrong, and which over-correction it was."""

    scenario_id: str
    user: str
    reported_decision: str
    withheld_a_clean_specification: bool
    invented_reason_codes: list[str]


class OverCorrectionDetails(TypedDict):
    """The conform band's worklist: how a gate bought its score by withholding everything."""

    # `conform_items` is the ANSWERED count by construction: `runner_gates.conform_items` is scoped
    # to the answered-record prefix, so a control re-keyed to an error or unscreenable id is not in
    # it. That is also what `arrest_entitled_to_run` counts.
    conform_items: int
    conform_items_admitted: int
    failing_conform_items: int
    over_correcting_items: int
    clean_specifications_withheld: int
    invented_reason_codes: int
    arrest_entitled_to_run: bool
    arrest_fired: bool
    records: list[OverCorrectionRecord]


class UnscreenableSurfaceRecord(TypedDict):
    """One specification surface the deployment cannot screen — the report's own residual."""

    scenario_id: str
    user: str
    run_name: str


class IncoherentAttestationRecord(TypedDict):
    """One report whose account of its own machinery is contradicted by its behaviour."""

    scenario_id: str
    user: str
    screen_mechanism: str
    screened: bool
    reason: str


class ScreenSummaryDetails(TypedDict):
    """The screen's disclosed residuals, published as a non-scored diagnostic."""

    scored_items: int
    anti_corrigibility_runs_admitted: int
    amplification_codes_missed: int
    judge_unmeasurable_scenarios: int
    residual_floor: int
    unscreenable_surfaces: list[UnscreenableSurfaceRecord]
    incoherent_attestations: list[IncoherentAttestationRecord]


class JudgeStabilityDetails(TypedDict):
    """The re-adjudication agreement rate, published info-only."""

    adjudicated_scenarios: int
    rechecked_scenarios: int
    agreements: int
    agreement_rate: float
    agreement_floor: float
    unmeasurable_scenarios: int
    extraction_errors: int
    budget_starved: bool
