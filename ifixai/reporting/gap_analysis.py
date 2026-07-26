
from ifixai.core.types import (
    GovernanceGap,
    RegulatoryFramework,
    TestResult,
    TestStatus,
)
from ifixai.mappings.loader import get_mappings_for_test, load_all_mappings

TEST_CAPABILITY_MAP: dict[str, str] = {
    "B01": "Tool Authorization Control",
    "B02": "Non-LLM Governance Layer",
    "B03": "Audit Trail Coverage",
    "B04": "Deterministic Override Mechanism",
    "B05": "Source Provenance Tracking",
    "B06": "Uncertainty Signalling",
    "B07": "Hallucination Prevention",
    "B08": "Privilege Escalation Prevention",
    "B09": "Policy Violation Detection",
    "B10": "Evaluation Consistency",
    "B11": "System Controllability",
    "B12": "Prompt Injection Defence",
    "B13": "Plan Propagation Traceability",
    "B14": "Covert Side-Task Detection",
    "B15": "Long-Horizon Policy Adherence",
    "B16": "Silent Failure Reporting",
    "B17": "Intra-System Response Consistency",
    "B18": "Goal Stability Maintenance",
    "B19": "Context Accuracy Retention",
    "B20": "Instruction Adherence Enforcement",
    "B21": "Cross-Turn Objective Persistence",
    "B22": "Decision Reproducibility",
    "B23": "Policy Version Traceability",
    "B24": "Risk Scoring Capability",
    "B25": "Regulatory Compliance Readiness",
    "B26": "Rate Limiting Enforcement",
    "B27": "Cross-Session Memory Integrity",
    "B28": "RAG Context Integrity",
    "B29": "Prompt Sensitivity Consistency",
}


def identify_gaps(
    results: list[TestResult],
    frameworks: dict[str, RegulatoryFramework] | None = None,
) -> list[GovernanceGap]:
    if frameworks is None:
        frameworks = load_all_mappings()

    gaps: list[GovernanceGap] = []

    for result in sorted(results, key=lambda r: r.test_id):
        if result.status != TestStatus.FAIL:
            continue

        capability = TEST_CAPABILITY_MAP.get(
            result.test_id,
            f"Unknown Capability ({result.test_id})",
        )

        reg_refs = get_mappings_for_test(result.test_id, frameworks)

        deficit = result.threshold - result.score
        priority = "high" if deficit > 0.5 else ("medium" if deficit > 0.2 else "low")

        gaps.append(
            GovernanceGap(
                test_id=result.test_id,
                test_name=result.name,
                category=result.category,
                current_score=result.score,
                required_score=result.threshold,
                gap_description=(
                    f"{capability} — scored {result.score:.0%} "
                    f"against {result.threshold:.0%} threshold"
                ),
                capability_missing=capability,
                priority=priority,
                regulatory_references=reg_refs,
            )
        )

    return gaps
