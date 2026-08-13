import re
import warnings
from collections.abc import Iterable
from typing import TypedDict

from ifixai.core.types import InspectionCategory
from ifixai.harness.base import BaseTest
from ifixai.inspections.b01_tool_governance.runner import (
    SPEC as B01_SPEC,
)
from ifixai.inspections.b01_tool_governance.runner import (
    B01ToolGovernance,
)
from ifixai.inspections.b02_non_llm_layer.runner import SPEC as B02_SPEC
from ifixai.inspections.b02_non_llm_layer.runner import B02NonLlmLayer
from ifixai.inspections.b03_auditability.runner import SPEC as B03_SPEC
from ifixai.inspections.b03_auditability.runner import B03Auditability
from ifixai.inspections.b04_deterministic_override.runner import (
    SPEC as B04_SPEC,
)
from ifixai.inspections.b04_deterministic_override.runner import (
    B04DeterministicOverride,
)
from ifixai.inspections.b05_source_provenance.runner import (
    SPEC as B05_SPEC,
)
from ifixai.inspections.b05_source_provenance.runner import (
    B05SourceProvenance,
)
from ifixai.inspections.b06_uncertainty_signalling.runner import (
    SPEC as B06_SPEC,
)
from ifixai.inspections.b06_uncertainty_signalling.runner import (
    B06UncertaintySignalling,
)
from ifixai.inspections.b07_hallucination_rate.runner import (
    SPEC as B07_SPEC,
)
from ifixai.inspections.b07_hallucination_rate.runner import (
    B07HallucinationRate,
)
from ifixai.inspections.b08_privilege_escalation.runner import (
    SPEC as B08_SPEC,
)
from ifixai.inspections.b08_privilege_escalation.runner import (
    B08PrivilegeEscalation,
)
from ifixai.inspections.b09_policy_violation.runner import (
    SPEC as B09_SPEC,
)
from ifixai.inspections.b09_policy_violation.runner import (
    B09PolicyViolation,
)
from ifixai.inspections.b10_evaluation_drift.runner import (
    SPEC as B10_SPEC,
)
from ifixai.inspections.b10_evaluation_drift.runner import (
    B10EvaluationDrift,
)
from ifixai.inspections.b11_system_controllability.runner import (
    SPEC as B11_SPEC,
)
from ifixai.inspections.b11_system_controllability.runner import (
    B11SystemControllability,
)
from ifixai.inspections.b12_prompt_injection.runner import (
    SPEC as B12_SPEC,
)
from ifixai.inspections.b12_prompt_injection.runner import (
    B12PromptInjection,
)
from ifixai.inspections.b13_plan_traceability.runner import (
    SPEC as B13_SPEC,
)
from ifixai.inspections.b13_plan_traceability.runner import (
    B13PlanTraceability,
)
from ifixai.inspections.b14_covert_side_task.runner import (
    SPEC as B14_SPEC,
)
from ifixai.inspections.b14_covert_side_task.runner import (
    B14CovertSideTask,
)
from ifixai.inspections.b15_long_horizon_drift.runner import (
    SPEC as B15_SPEC,
)
from ifixai.inspections.b15_long_horizon_drift.runner import (
    B15LongHorizonDrift,
)
from ifixai.inspections.b16_silent_failure.runner import (
    SPEC as B16_SPEC,
)
from ifixai.inspections.b16_silent_failure.runner import (
    B16SilentFailure,
)
from ifixai.inspections.b17_fact_consistency.runner import (
    SPEC as B17_SPEC,
)
from ifixai.inspections.b17_fact_consistency.runner import (
    B17FactConsistency,
)
from ifixai.inspections.b18_goal_stability.runner import (
    SPEC as B18_SPEC,
)
from ifixai.inspections.b18_goal_stability.runner import (
    B18GoalStability,
)
from ifixai.inspections.b19_context_accuracy.runner import (
    SPEC as B19_SPEC,
)
from ifixai.inspections.b19_context_accuracy.runner import (
    B19ContextAccuracy,
)
from ifixai.inspections.b20_instruction_adherence.runner import (
    SPEC as B20_SPEC,
)
from ifixai.inspections.b20_instruction_adherence.runner import (
    B20InstructionAdherence,
)
from ifixai.inspections.b21_cross_turn_objective.runner import (
    SPEC as B21_SPEC,
)
from ifixai.inspections.b21_cross_turn_objective.runner import (
    B21CrossTurnObjective,
)
from ifixai.inspections.b22_decision_reproducibility.runner import (
    SPEC as B22_SPEC,
)
from ifixai.inspections.b22_decision_reproducibility.runner import (
    B22DecisionReproducibility,
)
from ifixai.inspections.b23_policy_version_trace.runner import (
    SPEC as B23_SPEC,
)
from ifixai.inspections.b23_policy_version_trace.runner import (
    B23PolicyVersionTrace,
)
from ifixai.inspections.b24_risk_scoring.runner import SPEC as B24_SPEC
from ifixai.inspections.b24_risk_scoring.runner import B24RiskScoring
from ifixai.inspections.b25_regulatory_readiness.runner import (
    SPEC as B25_SPEC,
)
from ifixai.inspections.b25_regulatory_readiness.runner import (
    B25RegulatoryReadiness,
)
from ifixai.inspections.b26_rate_limiting.runner import (
    SPEC as B26_SPEC,
)
from ifixai.inspections.b26_rate_limiting.runner import (
    B26RateLimiting,
)
from ifixai.inspections.b27_session_integrity.runner import (
    SPEC as B27_SPEC,
)
from ifixai.inspections.b27_session_integrity.runner import (
    B27SessionIntegrity,
)
from ifixai.inspections.b28_rag_context_integrity.runner import (
    SPEC as B28_SPEC,
)
from ifixai.inspections.b28_rag_context_integrity.runner import (
    B28RAGContextIntegrity,
)
from ifixai.inspections.b29_prompt_sensitivity.runner import (
    SPEC as B29_SPEC,
)
from ifixai.inspections.b29_prompt_sensitivity.runner import (
    B29PromptSensitivity,
)
from ifixai.inspections.b30_malicious_deployer_rules.runner import (
    SPEC as B30_SPEC,
)
from ifixai.inspections.b30_malicious_deployer_rules.runner import (
    B30MaliciousDeployerRules,
)
from ifixai.inspections.b31_escalation_correctness.runner import (
    SPEC as B31_SPEC,
)
from ifixai.inspections.b31_escalation_correctness.runner import (
    B31EscalationCorrectness,
)
from ifixai.inspections.b32_off_topic_detection.runner import (
    SPEC as B32_SPEC,
)
from ifixai.inspections.b32_off_topic_detection.runner import (
    B32OffTopicDetection,
)
from ifixai.inspections.c02_low_confidence_abstention.runner import (
    SPEC as C02_SPEC,
)
from ifixai.inspections.c02_low_confidence_abstention.runner import (
    C02LowConfidenceAbstention,
)
from ifixai.inspections.c05_human_fallback_routing.runner import (
    SPEC as C05_SPEC,
)
from ifixai.inspections.c05_human_fallback_routing.runner import (
    C05HumanFallbackRouting,
)
from ifixai.inspections.c11_operational_outcome_conformance.runner import (
    SPEC as C11_SPEC,
)
from ifixai.inspections.c11_operational_outcome_conformance.runner import (
    C11OperationalOutcomeConformance,
)
from ifixai.inspections.m02_standing_authority_revalidation.runner import (
    SPEC as M02_SPEC,
)
from ifixai.inspections.m02_standing_authority_revalidation.runner import (
    M02StandingAuthorityRevalidation,
)
from ifixai.inspections.m03_finetune_corpus_contamination.runner import (
    SPEC as M03_SPEC,
)
from ifixai.inspections.m03_finetune_corpus_contamination.runner import (
    M03FineTuneCorpusContamination,
)
from ifixai.inspections.m06_model_identity_attestation.runner import (
    SPEC as M06_SPEC,
)
from ifixai.inspections.m06_model_identity_attestation.runner import (
    M06ModelIdentityAttestation,
)
from ifixai.inspections.m07_cross_org_delegation_scope.runner import (
    SPEC as M07_SPEC,
)
from ifixai.inspections.m07_cross_org_delegation_scope.runner import (
    M07CrossOrgDelegationScope,
)
from ifixai.inspections.p01_destruction_resistance.runner import (
    SPEC as P01_SPEC,
)
from ifixai.inspections.p01_destruction_resistance.runner import (
    P01DestructionResistance,
)
from ifixai.inspections.p08_self_audit_trail_integrity.runner import (
    SPEC as P08_SPEC,
)
from ifixai.inspections.p08_self_audit_trail_integrity.runner import (
    P08SelfAuditTrailIntegrity,
)
from ifixai.inspections.p13_hidden_side_objective_persistence.runner import (
    SPEC as P13_SPEC,
)
from ifixai.inspections.p13_hidden_side_objective_persistence.runner import (
    P13HiddenSideObjectivePersistence,
)
from ifixai.inspections.p19_honeypot_tool_restraint.runner import (
    SPEC as P19_SPEC,
)
from ifixai.inspections.p19_honeypot_tool_restraint.runner import (
    P19HoneypotToolRestraint,
)
from ifixai.inspections.p22_goal_edit_acceptance.runner import (
    SPEC as P22_SPEC,
)
from ifixai.inspections.p22_goal_edit_acceptance.runner import (
    P22GoalEditAcceptance,
)
from ifixai.inspections.p27_privilege_creep.runner import (
    SPEC as P27_SPEC,
)
from ifixai.inspections.p27_privilege_creep.runner import (
    P27PrivilegeCreep,
)
from ifixai.inspections.p32_emergent_systemic_harm.runner import (
    SPEC as P32_SPEC,
)
from ifixai.inspections.p32_emergent_systemic_harm.runner import (
    P32EmergentSystemicHarm,
)
from ifixai.inspections.s02_configurer_stakeholder_conflict.runner import (
    SPEC as S02_SPEC,
)
from ifixai.inspections.s02_configurer_stakeholder_conflict.runner import (
    S02ConfigurerStakeholderConflict,
)
from ifixai.inspections.x04_detection_performance_gate.runner import (
    SPEC as X04_SPEC,
)
from ifixai.inspections.x04_detection_performance_gate.runner import (
    X04DetectionPerformanceGate,
)
from ifixai.inspections.x11_pre_action_confirmation_gate.runner import (
    SPEC as X11_SPEC,
)
from ifixai.inspections.x11_pre_action_confirmation_gate.runner import (
    X11PreActionConfirmationGate,
)
from ifixai.scoring.category_weights import STRATEGIC_TEST_IDS

ALL_SPECS = [
    B01_SPEC,
    B02_SPEC,
    B03_SPEC,
    B04_SPEC,
    B05_SPEC,
    B06_SPEC,
    B07_SPEC,
    B08_SPEC,
    B09_SPEC,
    B10_SPEC,
    B11_SPEC,
    B12_SPEC,
    B13_SPEC,
    B14_SPEC,
    B15_SPEC,
    B16_SPEC,
    B17_SPEC,
    B18_SPEC,
    B19_SPEC,
    B20_SPEC,
    B21_SPEC,
    B22_SPEC,
    B23_SPEC,
    B24_SPEC,
    B25_SPEC,
    B26_SPEC,
    B27_SPEC,
    B28_SPEC,
    B29_SPEC,
    B30_SPEC,
    B31_SPEC,
    B32_SPEC,
    P01_SPEC,
    P08_SPEC,
    P13_SPEC,
    P19_SPEC,
    P22_SPEC,
    P27_SPEC,
    P32_SPEC,
    C02_SPEC,
    C05_SPEC,
    C11_SPEC,
    S02_SPEC,
    X04_SPEC,
    X11_SPEC,
    M02_SPEC,
    M03_SPEC,
    M06_SPEC,
    M07_SPEC,
]

SPEC_BY_ID: dict[str, object] = {spec.test_id: spec for spec in ALL_SPECS}

# B-series: B01..B32 (Categories I–V). P-series: P01..P32 (Categories VI–XII —
# SABOTAGE / SUBVERSION / CONCEALMENT / SANDBAGGING / INSUBORDINATION / USURPATION /
# SYSTEMIC_RISK); P19 (honeypot-tool restraint) is SANDBAGGING (Category IX —
# capability concealment & evaluation gaming). C-series: C01..C16 (Capability-Reliability,
# Categories XIV–XXI); C02 (abstention), C05 (human-fallback routing) and C11
# (operational-outcome conformance & decay) are the first three, all MISCALIBRATION (XIV).
# S-series: S01..S08 (Stakeholder & Multi-Principal Integrity, Category XVIII); S02
# (configurer-vs-stakeholder conflict) is the first. X-series: X01..X11 (Gap-closure,
# Categories XXII–XXVI); X04 (deployed-detection-performance acceptance gate,
# PERCEPTION_GOVERNANCE XXIII) and X11 (automation-bias / pre-action confirmation gate,
# OVERSIGHT_ATROPHY XXVI — human-oversight atrophy) are the two current members. M-series:
# M01..M12 (M-series, Categories XXVII–XXVIII: it opens at XXVII because the X-series' five
# classes consume XXII–XXVI in full, and its failure classes then take contiguous numerals the
# same way the C-series takes XIV–XXI). PERSISTENCE (XXVII — integrity of state that outlives
# the session) is its first class, home of M02 (standing-automation authority re-validation) and
# M03 (fine-tune corpus contamination gate); IDENTITY_ATTESTATION (XXVIII — knowing what, and
# whom, you are running) is its second, home of M06 (runtime model-identity attestation and
# silent-substitution detection) and M07 (cross-organization delegation scope attenuation — the
# counterparty half of the same category: its definition already names agent-to-agent protocols
# making the counterparty an open question, so M07 opens NO new numeral).
# The 'M' alternative with a 01..12 NN range was added for M02.
# All ranges share the NN shape; the S alternative (01..08) admits S02 ∈ 0[1-8], the X
# alternative (01..11) admits both X04 and X11 ∈ 0[1-9]|1[0-1] (the X04 addition already widened
# the pattern to the full X-series range), and the M alternative (01..12) — added for M02 —
# already admits M03, M06 and M07, so none required a pattern change (pinned by
# test_id_pattern_already_admits_m03 / _m06 / _m07).
_TEST_ID_PATTERN = re.compile(
    r"^(B(0[1-9]|[12][0-9]|3[0-2])|P(0[1-9]|[12][0-9]|3[0-2])"
    r"|C(0[1-9]|1[0-6])|S(0[1-8])|X(0[1-9]|1[0-1])|M(0[1-9]|1[0-2]))$"
)
_LEGACY_PREFIXES = ("SSCI-",)


def normalize_test_id(value: str) -> str:
    candidate = value.strip().upper()
    for prefix in _LEGACY_PREFIXES:
        if candidate.startswith(prefix):
            warnings.warn(
                f"Test id '{value}' uses the deprecated '{prefix}' prefix; "
                f"use the bare form '{candidate[len(prefix):]}' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            candidate = candidate[len(prefix) :]
            break
    if not _TEST_ID_PATTERN.match(candidate):
        raise ValueError(
            f"Unknown test id: {value!r}. Expected B01..B32, P01..P32, C01..C16, "
            "S01..S08, X01..X11 or M01..M12 (bare form)."
        )
    return candidate


def create_inspection(spec_id: str) -> BaseTest:
    registry: dict[str, type[BaseTest]] = {
        "B01": B01ToolGovernance,
        "B02": B02NonLlmLayer,
        "B03": B03Auditability,
        "B04": B04DeterministicOverride,
        "B05": B05SourceProvenance,
        "B06": B06UncertaintySignalling,
        "B07": B07HallucinationRate,
        "B08": B08PrivilegeEscalation,
        "B09": B09PolicyViolation,
        "B10": B10EvaluationDrift,
        "B11": B11SystemControllability,
        "B12": B12PromptInjection,
        "B13": B13PlanTraceability,
        "B14": B14CovertSideTask,
        "B15": B15LongHorizonDrift,
        "B16": B16SilentFailure,
        "B17": B17FactConsistency,
        "B18": B18GoalStability,
        "B19": B19ContextAccuracy,
        "B20": B20InstructionAdherence,
        "B21": B21CrossTurnObjective,
        "B22": B22DecisionReproducibility,
        "B23": B23PolicyVersionTrace,
        "B24": B24RiskScoring,
        "B25": B25RegulatoryReadiness,
        "B26": B26RateLimiting,
        "B27": B27SessionIntegrity,
        "B28": B28RAGContextIntegrity,
        "B29": B29PromptSensitivity,
        "B30": B30MaliciousDeployerRules,
        "B31": B31EscalationCorrectness,
        "B32": B32OffTopicDetection,
        "P01": P01DestructionResistance,
        "P08": P08SelfAuditTrailIntegrity,
        "P13": P13HiddenSideObjectivePersistence,
        "P19": P19HoneypotToolRestraint,
        "P22": P22GoalEditAcceptance,
        "P27": P27PrivilegeCreep,
        "P32": P32EmergentSystemicHarm,
        "C02": C02LowConfidenceAbstention,
        "C05": C05HumanFallbackRouting,
        "C11": C11OperationalOutcomeConformance,
        "S02": S02ConfigurerStakeholderConflict,
        "X04": X04DetectionPerformanceGate,
        "X11": X11PreActionConfirmationGate,
        "M02": M02StandingAuthorityRevalidation,
        "M03": M03FineTuneCorpusContamination,
        "M06": M06ModelIdentityAttestation,
        "M07": M07CrossOrgDelegationScope,
    }
    inspection_class = registry.get(spec_id)
    if inspection_class is None:
        raise ValueError(f"Unknown test: {spec_id}")
    return inspection_class()


INSPECTION_REGISTRY: dict[str, BaseTest] = {
    spec_id: create_inspection(spec_id) for spec_id in SPEC_BY_ID
}

ALL_TEST_SPECS = ALL_SPECS

CATEGORIES = {
    1: InspectionCategory.FABRICATION,
    2: InspectionCategory.MANIPULATION,
    3: InspectionCategory.DECEPTION,
    4: InspectionCategory.UNPREDICTABILITY,
    5: InspectionCategory.OPACITY,
    6: InspectionCategory.SABOTAGE,
    7: InspectionCategory.SUBVERSION,
    8: InspectionCategory.CONCEALMENT,
    9: InspectionCategory.SANDBAGGING,
    10: InspectionCategory.INSUBORDINATION,
    11: InspectionCategory.USURPATION,
    12: InspectionCategory.SYSTEMIC_RISK,
    # Index 13 intentionally unallocated; the C-series begins at Category XIV.
    14: InspectionCategory.MISCALIBRATION,
    # Indices 15–17 intentionally unallocated; the S-series begins at Category XVIII.
    18: InspectionCategory.STAKEHOLDER_CONFLICT,
    # Indices 19–22 intentionally unallocated; the X-series — Gap-closure — begins at
    # Category XXII. XXII, XXIV and XXV are reserved; PERCEPTION_GOVERNANCE (Category XXIII)
    # is the perception-deployment-governance member, home of X04, and OVERSIGHT_ATROPHY
    # (Category XXVI) is the human-oversight-atrophy member, home of X11.
    23: InspectionCategory.PERCEPTION_GOVERNANCE,
    26: InspectionCategory.OVERSIGHT_ATROPHY,
    # Category XXVII — PERSISTENCE, the M-series' (M01–M12) FIRST failure class, home of M02 and
    # M03. It opens at XXVII because the X-series' five failure classes consume XXII–XXVI in full.
    27: InspectionCategory.PERSISTENCE,
    # Category XXVIII — IDENTITY_ATTESTATION, the M-series' SECOND failure class (contiguous
    # numerals, as with the C- and X-series), home of M06 (which model answered) and M07 (which
    # counterparty is about to receive authority, and how much).
    28: InspectionCategory.IDENTITY_ATTESTATION,
}

CATEGORY_DESCRIPTIONS = {
    1: "FABRICATION — Accuracy & Calibration",
    2: "MANIPULATION — Safety & Containment",
    3: "DECEPTION — Hidden Strategy",
    4: "UNPREDICTABILITY — Stability & Consistency",
    5: "OPACITY — Transparency & Auditability",
    6: "SABOTAGE — Operational Harm to the Organization",
    7: "SUBVERSION — Oversight Evasion & Audit Integrity",
    8: "CONCEALMENT — Hidden Agendas & Long-Horizon Deception",
    9: "SANDBAGGING — Capability Concealment & Evaluation Gaming",
    10: "INSUBORDINATION — Corrigibility & Self-Preservation",
    11: "USURPATION — Covert Capability & Power Elevation",
    12: "SYSTEMIC_RISK — Multi-Agent Collusion & Emergent Harm",
    14: "MISCALIBRATION — Capability-Reliability: Governance of Uncertainty",
    18: "STAKEHOLDER_CONFLICT — Stakeholder & Multi-Principal Integrity",
    23: "PERCEPTION_GOVERNANCE — Perception-Deployment Governance & Assurance Gates",
    26: "OVERSIGHT_ATROPHY — Human-Oversight Atrophy & Pre-Action Confirmation Gates",
    27: "PERSISTENCE — Integrity of State That Outlives the Session",
    28: "IDENTITY_ATTESTATION — Knowing What (and Whom) You Are Running",
}

STRATEGIC_TESTS = STRATEGIC_TEST_IDS


def get_tests_by_category(category_id: int) -> list[object]:
    category = CATEGORIES.get(category_id)
    if category is None:
        return []
    return [s for s in ALL_SPECS if s.category == category]


# Category names accepted by the `--category/-c` CLI filter, in enum order.
# Drives both selection (case-insensitive) and the "Available:" error hint.
CATEGORY_NAMES: list[str] = [category.value for category in InspectionCategory]


class CategoryResolution(TypedDict):
    """Outcome of expanding `--category` names into concrete test IDs."""

    test_ids: list[str]
    unknown: list[str]


def _import_suites():  # pragma: no cover
    from ifixai.harness import suites

    return suites


def resolve_suite(name: str):
    """Re-export of ifixai.harness.suites.resolve_suite."""
    return _import_suites().resolve_suite(name)


def suite_catalog():
    """Re-export of ifixai.harness.suites.suite_catalog."""
    return _import_suites().suite_catalog()


def resolve_category_test_ids(names: Iterable[str]) -> CategoryResolution:
    """Expand failure-category names into the test IDs they contain.

    Names are matched case-insensitively against ``InspectionCategory``
    values. The returned ``test_ids`` follow ``ALL_SPECS`` order and are
    de-duplicated across the requested categories, so the result feeds the
    existing selected-subset run path unchanged. ``unknown`` carries any
    names that matched no category so the caller can report them.
    """
    by_name = {category.value: category for category in InspectionCategory}
    matched: list[InspectionCategory] = []
    unknown: list[str] = []
    for raw in names:
        category = by_name.get(raw.strip().upper())
        if category is None:
            unknown.append(raw)
        elif category not in matched:
            matched.append(category)
    test_ids: list[str] = [
        spec.test_id for spec in ALL_SPECS if spec.category in matched
    ]
    return {"test_ids": test_ids, "unknown": unknown}
