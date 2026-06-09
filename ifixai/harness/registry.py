import re
import warnings
from collections.abc import Iterable
from typing import TypedDict

from ifixai.inspections.b01_tool_governance.runner import (
    SPEC as B01_SPEC,
    B01ToolGovernance,
)
from ifixai.inspections.b02_non_llm_layer.runner import SPEC as B02_SPEC, B02NonLlmLayer
from ifixai.inspections.b03_auditability.runner import SPEC as B03_SPEC, B03Auditability
from ifixai.inspections.b04_deterministic_override.runner import (
    SPEC as B04_SPEC,
    B04DeterministicOverride,
)
from ifixai.inspections.b05_source_provenance.runner import (
    SPEC as B05_SPEC,
    B05SourceProvenance,
)
from ifixai.inspections.b06_uncertainty_signalling.runner import (
    SPEC as B06_SPEC,
    B06UncertaintySignalling,
)
from ifixai.inspections.b07_hallucination_rate.runner import (
    SPEC as B07_SPEC,
    B07HallucinationRate,
)
from ifixai.inspections.b08_privilege_escalation.runner import (
    SPEC as B08_SPEC,
    B08PrivilegeEscalation,
)
from ifixai.inspections.b09_policy_violation.runner import (
    SPEC as B09_SPEC,
    B09PolicyViolation,
)
from ifixai.inspections.b10_evaluation_drift.runner import (
    SPEC as B10_SPEC,
    B10EvaluationDrift,
)
from ifixai.inspections.b11_system_controllability.runner import (
    SPEC as B11_SPEC,
    B11SystemControllability,
)
from ifixai.inspections.b12_prompt_injection.runner import (
    SPEC as B12_SPEC,
    B12PromptInjection,
)
from ifixai.inspections.b13_plan_traceability.runner import (
    SPEC as B13_SPEC,
    B13PlanTraceability,
)
from ifixai.inspections.b14_covert_side_task.runner import (
    SPEC as B14_SPEC,
    B14CovertSideTask,
)
from ifixai.inspections.b15_long_horizon_drift.runner import (
    SPEC as B15_SPEC,
    B15LongHorizonDrift,
)
from ifixai.inspections.b16_silent_failure.runner import (
    SPEC as B16_SPEC,
    B16SilentFailure,
)
from ifixai.inspections.b17_fact_consistency.runner import (
    SPEC as B17_SPEC,
    B17FactConsistency,
)
from ifixai.inspections.b18_goal_stability.runner import (
    SPEC as B18_SPEC,
    B18GoalStability,
)
from ifixai.inspections.b19_context_accuracy.runner import (
    SPEC as B19_SPEC,
    B19ContextAccuracy,
)
from ifixai.inspections.b20_instruction_adherence.runner import (
    SPEC as B20_SPEC,
    B20InstructionAdherence,
)
from ifixai.inspections.b21_cross_turn_objective.runner import (
    SPEC as B21_SPEC,
    B21CrossTurnObjective,
)
from ifixai.inspections.b22_decision_reproducibility.runner import (
    SPEC as B22_SPEC,
    B22DecisionReproducibility,
)
from ifixai.inspections.b23_policy_version_trace.runner import (
    SPEC as B23_SPEC,
    B23PolicyVersionTrace,
)
from ifixai.inspections.b24_risk_scoring.runner import SPEC as B24_SPEC, B24RiskScoring
from ifixai.inspections.b25_regulatory_readiness.runner import (
    SPEC as B25_SPEC,
    B25RegulatoryReadiness,
)
from ifixai.inspections.b26_rate_limiting.runner import (
    SPEC as B26_SPEC,
    B26RateLimiting,
)
from ifixai.inspections.b27_session_integrity.runner import (
    SPEC as B27_SPEC,
    B27SessionIntegrity,
)
from ifixai.inspections.b28_rag_context_integrity.runner import (
    SPEC as B28_SPEC,
    B28RAGContextIntegrity,
)
from ifixai.inspections.b29_prompt_sensitivity.runner import (
    SPEC as B29_SPEC,
    B29PromptSensitivity,
)
from ifixai.inspections.b30_malicious_deployer_rules.runner import (
    SPEC as B30_SPEC,
    B30MaliciousDeployerRules,
)
from ifixai.inspections.b31_escalation_correctness.runner import (
    SPEC as B31_SPEC,
    B31EscalationCorrectness,
)
from ifixai.inspections.b32_off_topic_detection.runner import (
    SPEC as B32_SPEC,
    B32OffTopicDetection,
)
from ifixai.inspections.p01_destruction_resistance.runner import (
    SPEC as P01_SPEC,
    P01DestructionResistance,
)
from ifixai.inspections.p08_self_audit_trail_integrity.runner import (
    SPEC as P08_SPEC,
    P08SelfAuditTrailIntegrity,
)
from ifixai.inspections.p13_hidden_side_objective_persistence.runner import (
    SPEC as P13_SPEC,
    P13HiddenSideObjectivePersistence,
)
from ifixai.inspections.p19_honeypot_tool_restraint.runner import (
    SPEC as P19_SPEC,
    P19HoneypotToolRestraint,
)
from ifixai.inspections.p22_goal_edit_acceptance.runner import (
    SPEC as P22_SPEC,
    P22GoalEditAcceptance,
)
from ifixai.inspections.p27_privilege_creep.runner import (
    SPEC as P27_SPEC,
    P27PrivilegeCreep,
)
from ifixai.inspections.p32_emergent_systemic_harm.runner import (
    SPEC as P32_SPEC,
    P32EmergentSystemicHarm,
)
from ifixai.inspections.c02_low_confidence_abstention.runner import (
    SPEC as C02_SPEC,
    C02LowConfidenceAbstention,
)
from ifixai.inspections.c05_human_fallback_routing.runner import (
    SPEC as C05_SPEC,
    C05HumanFallbackRouting,
)
from ifixai.inspections.c11_operational_outcome_conformance.runner import (
    SPEC as C11_SPEC,
    C11OperationalOutcomeConformance,
)
from ifixai.inspections.s02_configurer_stakeholder_conflict.runner import (
    SPEC as S02_SPEC,
    S02ConfigurerStakeholderConflict,
)
from ifixai.inspections.x04_detection_performance_gate.runner import (
    SPEC as X04_SPEC,
    X04DetectionPerformanceGate,
)
from ifixai.inspections.x11_pre_action_confirmation_gate.runner import (
    SPEC as X11_SPEC,
    X11PreActionConfirmationGate,
)
from ifixai.harness.base import BaseTest
from ifixai.scoring.category_weights import STRATEGIC_TEST_IDS
from ifixai.core.types import InspectionCategory

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
]

SPEC_BY_ID: dict[str, object] = {spec.test_id: spec for spec in ALL_SPECS}

# B-series: B01..B32 (Categories I–V). P-series: P01..P32 (Categories VI–XI —
# SABOTAGE / SUBVERSION / CONCEALMENT / INSUBORDINATION / USURPATION / SYSTEMIC_RISK).
# C-series: C01..C16 (Capability-Reliability, Categories XIII–XX); C02 (abstention),
# C05 (human-fallback routing) and C11 (operational-outcome conformance & decay) are
# the first three, all MISCALIBRATION (XIII). S-series: S01..S08 (Stakeholder &
# Multi-Principal Integrity, Category XVII); S02 (configurer-vs-stakeholder conflict) is the
# first. X-series: X01..X11 (Gap-closure, Categories XXI–XXV); X04 (deployed-detection-
# performance acceptance gate, PERCEPTION_GOVERNANCE XXII) and X11 (automation-bias /
# pre-action confirmation gate, OVERSIGHT_ATROPHY XXV — human-oversight atrophy) are the
# two current members. All ranges share the NN shape; the S alternative (01..08) admits
# S02 ∈ 0[1-8] and the X alternative (01..11) admits both X04 and X11 ∈ 0[1-9]|1[0-1]
# (the X04 addition already widened the pattern to the full X-series range, so X11 needs
# no further widening).
_TEST_ID_PATTERN = re.compile(
    r"^(B(0[1-9]|[12][0-9]|3[0-2])|P(0[1-9]|[12][0-9]|3[0-2])"
    r"|C(0[1-9]|1[0-6])|S(0[1-8])|X(0[1-9]|1[0-1]))$"
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
            f"Unknown test id: {value!r}. Expected B01..B32 or P01..P32 (bare form)."
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
    9: InspectionCategory.INSUBORDINATION,
    10: InspectionCategory.USURPATION,
    11: InspectionCategory.SYSTEMIC_RISK,
    # Index 12 intentionally unallocated; the C-series begins at Category XIII.
    13: InspectionCategory.MISCALIBRATION,
    # Indices 14–16 intentionally unallocated; the S-series begins at Category XVII.
    17: InspectionCategory.STAKEHOLDER_CONFLICT,
    # Indices 18–21 intentionally unallocated; the X-series — Gap-closure — begins at
    # Category XXI. XXI, XXIII and XXIV are reserved; PERCEPTION_GOVERNANCE (Category XXII)
    # is the perception-deployment-governance member, home of X04, and OVERSIGHT_ATROPHY
    # (Category XXV) is the human-oversight-atrophy member, home of X11.
    22: InspectionCategory.PERCEPTION_GOVERNANCE,
    25: InspectionCategory.OVERSIGHT_ATROPHY,
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
    9: "INSUBORDINATION — Corrigibility & Self-Preservation",
    10: "USURPATION — Covert Capability & Power Elevation",
    11: "SYSTEMIC_RISK — Multi-Agent Collusion & Emergent Harm",
    13: "MISCALIBRATION — Capability-Reliability: Governance of Uncertainty",
    17: "STAKEHOLDER_CONFLICT — Stakeholder & Multi-Principal Integrity",
    22: "PERCEPTION_GOVERNANCE — Perception-Deployment Governance & Assurance Gates",
    25: "OVERSIGHT_ATROPHY — Human-Oversight Atrophy & Pre-Action Confirmation Gates",
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
