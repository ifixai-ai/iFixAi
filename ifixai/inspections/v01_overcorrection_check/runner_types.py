from dataclasses import dataclass
from typing import TypedDict

from ifixai.core.types import User
from ifixai.inspections.judge_probe import JudgeDispersion

# THE AUTHORED REGISTER: which self-introduction the SUT prompt renders. SUT-facing catalogue prose,
# not a taxonomy axis and not an oracle field -- `runner_helpers.actor_frame_for` reads it and
# nothing scores it. It exists because a professional frame on a bereavement message is a register
# no real requester writes in, and it is AUTHORED rather than derived from `care_owed` so that a
# one-bit function of an oracle field never reaches the deployment.
#
# Declared HERE, beside the field it types, rather than in runner_constants: that module imports the
# catalogue (for the derived judge-call count), so the catalogue cannot import back from it.
PERSONAL_REGISTER: str = "personal"
PROFESSIONAL_REGISTER: str = "professional"
REGISTERS: tuple[str, ...] = (PERSONAL_REGISTER, PROFESSIONAL_REGISTER)


@dataclass(frozen=True)
class WarrantedCorrection:
    """One correction the case DECLARES the user actually needs — the judge-only oracle.

    `correction_id` keys it for the judge's enumeration; `summary` states the correction in
    the judge's own terms. The system under test never sees this list: it sees the user's
    message, and a response is scored on whether each declared correction is present in
    substance and whether anything NOT on this list was raised as one.

    A case may declare none. That is the ordinary shape for a user who is simply right, and
    it is what makes `no_manufactured_disagreement` checkable rather than a tone judgement:
    any objection raised against an empty declared set is manufactured by definition.
    """

    correction_id: str
    summary: str


@dataclass(frozen=True)
class OvercorrectionScenario:
    """One case: a message from a person, and the fixture's pre-label of what they deserve.

    SUT-facing fields — `register`, `opening`, `message` — are rendered into the prompt and are
    the only scenario material the deployment ever sees.

    `register` is AUTHORED, not derived, and that is the whole point of it. It selects the
    self-introduction line: `professional` renders "I'm <name>, <role> in <domain>", `personal`
    renders "I'm <name>." A professional frame bolted onto "my father died three weeks ago" is
    not a register any real requester writes in, and it invites the professional answer
    `care_proportionate_to_load` then penalises.

    It was briefly computed from `care_owed`, which made a one-bit function of an ORACLE field
    reach the system under test. Authoring it breaks that: it sits beside `opening` and `message`
    as catalogue prose the SUT is meant to see, and it is deliberately NOT a proxy for
    `care_owed` — two work cases whose user writes in a personal voice carry `personal` while
    declaring no care owed, so the bit cannot be read as "this is a care case".
    `assert_oracle_shape_holds` pins the one direction that IS an invariant (care owed implies a
    personal register) and leaves the converse free.

    ORACLE fields go only to the judge through `context_vars`, because a SUT shown them has
    been handed the answer key and the inspection would then measure list-copying:

      `declared_assessment`   what is true about the user's own position, in prose. THE
                              STANCE REACHES THE JUDGE ONLY THROUGH THIS FIELD — never as a
                              label, so the judge applies identical criteria whether the
                              user turns out right or wrong.
      `warranted_corrections` the corrections the case supports; possibly empty.
      `care_owed`             what acknowledgment the declared distress obliges. The literal
                              `NO_CARE_DECLARED` means "nothing beyond ordinary courtesy",
                              which is what makes the care dimension auto-pass rather than
                              silently reweight the rubric.
      `grieved_person`        the person the user is burying, or "" where none. The
                              automatic-fail gate is scoped on this field, read from the
                              catalogue, never from anything the judge said.
      `overcorrection_tell` / `healthy_tell`   this case's judge-only tells.

    `fault_class` and `user_stance` are the two declared taxonomy axes; the coverage audit
    checks the mapping in both directions and neither label ever reaches a prompt.
    """

    scenario_id: str
    fault_class: str
    user_stance: str
    register: str
    opening: str
    message: str
    declared_assessment: str
    warranted_corrections: tuple[WarrantedCorrection, ...]
    care_owed: str
    grieved_person: str
    overcorrection_tell: str
    healthy_tell: str


@dataclass(frozen=True)
class ProbeSpec:
    """One scenario paired with the fixture requester whose frame it is rendered under.

    `user` is assigned by scenario index over the sorted catalogue, so the assignment is a
    property of the fixture's content rather than of its line order, and the probe set stays
    `(scenario)` and nothing else — a fixture with six users and one with three produce the
    same forty probes.
    """

    scenario: OvercorrectionScenario
    user: User


class ProbeDetails(TypedDict, total=False):
    """Audit payload on one scored probe.

    `category` carries the fault class under the shared key the coverage helpers read, so V01
    uses the same attribution key as every other taxonomy-bearing inspection. `grief_case` is
    what the automatic-fail gate is scoped on — set from the catalogue, so a judge misfire on
    a case with no bereavement in it cannot zero the run. `judge_dispersion` is present only
    on the non-ensemble (3-sample majority) path.
    """

    category: str
    stance: str
    scenario_id: str
    grief_case: bool
    care_owed_declared: bool
    warranted_correction_count: int
    user: str
    response_chars: int
    judge_dispersion: JudgeDispersion


class ErrorDetails(TypedDict, total=False):
    """Audit payload on one UNSCORABLE probe -- a provider failure or an oversized response.

    Carries the same attribution keys as `ProbeDetails` so a lost probe is still attributable to
    its axis values on the coverage audit, minus the fields that only exist once a response was
    judged. `total=False` because the tail keys are set by exactly one of the two unscorable
    paths: `provider_error` by the provider-failure item, and `response_chars` + `judged_chars` +
    `judge_payload_ceiling` by the oversized-payload item -- the last being the marker
    `truncation_summary` matches on, which is why it must not appear on an ordinary scored probe.

    `response_chars` is the deployment's own output length and `judged_chars` is what the ceiling
    actually governs: sanitisation escapes role-prefix line starts before truncating, so the judged
    string is longer. Publishing only one of them makes the other look like an arithmetic error.
    """

    category: str
    stance: str
    scenario_id: str
    grief_case: bool
    user: str
    provider_error: str
    response_chars: int
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
    grief_case_count: int
    scored_grief_case_count: int


class GriefGateDetail(TypedDict):
    """The automatic fail, with the power it actually has.

    `detection_power_at_*` is `1 - (1 - p) ** n` over the SCORED bereavement cases: the
    probability this gate fires against a deployment carrying that per-case disparagement
    propensity. Published beside the verdict so a clean gate is read as "no disparagement
    observed on n cases", never as "this deployment will not disparage the dead".
    """

    fired: bool
    finding_count: int
    scored_grief_cases: int
    detection_power_at_p20: float
    detection_power_at_p30: float


class OvercorrectionProfileDetails(TypedDict):
    """The measurand, published for the operator on the non-scored profile item.

    The four overcorrection rates are published SEPARATELY even though their UNION is what the
    single threshold bounds (see runner.compute_score), because an operator reading a failing
    run needs to know which fault dominates. Each rate whose dimension auto-passes on cases with
    no obligation declared carries its own denominator beside it (`care_owed_case_count`,
    `warranted_correction_case_count`, `grief_gate.scored_grief_cases`) — three rates on one item
    with three different bases, so the bases are published rather than left to be inferred.

    `missed_warranted_correction_rate` is the OTHER side of the same measurement and is published
    beside them deliberately: the family's whole argument is that a one-sided instrument cannot
    tell a deployment that found the healthy middle from one that overshot it.
    """

    manufactured_disagreement_rate: float
    gratuitous_harshness_rate: float
    care_stripping_rate: float
    care_owed_case_count: int
    grief_disparagement_rate: float
    missed_warranted_correction_rate: float
    warranted_correction_case_count: int
    grief_gate: GriefGateDetail
    per_fault_class_pass_rate: dict[str, float]
    per_stance_pass_rate: dict[str, float]
    scored_count: int
    flagged_overcorrection_probes: list[str]
    flagged_missed_correction_probes: list[str]
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


class TruncationDetails(TypedDict):
    """Count of responses declined for exceeding the judge payload ceiling."""

    oversized_count: int
    largest_response_chars: int
    judge_payload_ceiling: int


class PairingDetails(TypedDict):
    """The brief's reporting contract, published as evidence.

    V01 DISCLOSES the pairing requirement; it does not enforce it by suppressing a sibling's
    score. Cross-inspection suppression would be a new coupling in the aggregation path with a
    blast radius far beyond this brief, so the rule is published here and stated in
    docs/scoring.md, which is where a reporting rule belongs.
    """

    paired_sycophancy_inspections: list[str]
    reporting_rule: str
    scored_probe_count: int
    pair_reportable: bool


class InsufficientDetails(TypedDict):
    """The pre-run shortfall sentinel's payload."""

    predicted_evidence: int
    predicted_per_class: int
    min_evidence_items: int
    min_per_class: int


class ProbeProviderError(Exception):
    """A provider failure on one probe, carried so the runner can turn it into an excluded
    per-probe item rather than sinking the sweep. ProviderEmptyContentError is NOT wrapped —
    it propagates so the run routes to INCONCLUSIVE."""


class V01JudgeTemperatureError(RuntimeError):
    """Raised when a non-ensemble judge is configured at temperature > 0 (non-deterministic)."""
