"""Bounded permission-removal search that preserves declared completion goals."""
from __future__ import annotations

from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .integrity import create_exploration_bundle, validate_exploration_bundle
from .models import ExplorationBundle, ExplorationPolicy, SearchConfig
from .search import canonical_digest


class CandidateSummary(BaseModel):
    """Compact candidate coverage; full witnesses retained only for selection."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: Literal["COUNTEREXAMPLE", "VERIFIED", "GOAL_BLOCKED", "INCONCLUSIVE"]
    property_status: dict[str, Literal["COUNTEREXAMPLE", "VERIFIED", "INCONCLUSIVE"]]
    goal_status: dict[str, Literal["REACHABLE", "UNREACHABLE", "UNKNOWN"]]
    states_discovered: int
    states_expanded: int
    transitions_examined: int
    transitions_enabled: int
    frontier_exhausted: bool
    cutoff_reasons: tuple[str, ...]
    coverage_issues: tuple[str, ...]
    policy_digest: str
    config_digest: str
    report_digest: str
    bundle_digest: str


class RepairCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    removed_rule_indices: tuple[int, ...]
    report: CandidateSummary


class RepairReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["FOUND", "EXHAUSTED", "INCONCLUSIVE"]
    original_bundle_digest: str
    candidates: tuple[RepairCandidate, ...]
    selected_bundle: ExplorationBundle | None = None
    max_removals: int
    max_candidates: int
    transition_budget: int
    transitions_examined: int
    minimality_proven: bool
    cutoff_reasons: tuple[str, ...]
    limitations: tuple[str, ...] = (
        "Search removes declared permission rules only; it does not modify guards, budgets, code or deployed authorization.",
        "Accepted repairs preserve only the declared model properties and existential completion goals.",
        "Exhaustion is limited to the specified removal cardinality and does not prove that no other repair exists.",
    )


def search_repairs(bundle: ExplorationBundle, *, max_removals: int = 1, max_candidates: int = 64, transition_budget: int = 250000) -> RepairReport:
    bundle = validate_exploration_bundle(bundle)
    for value, lower, upper in ((max_removals, 1, 2), (max_candidates, 1, 256), (transition_budget, 1, 2000000)):
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError("repair limits must be bounded strict integers")
    if bundle.report.decision != "COUNTEREXAMPLE":
        raise ValueError("permission repair requires a retained counterexample")
    candidates: list[RepairCandidate] = []
    spent = 0
    cutoff: list[str] = []
    inconclusive_sizes: set[int] = set()
    selected: ExplorationBundle | None = None
    minimal = False
    status: Literal["FOUND", "EXHAUSTED", "INCONCLUSIVE"] = "EXHAUSTED"
    for size in range(1, min(max_removals, len(bundle.policy.permissions)) + 1):
        for removed in combinations(range(len(bundle.policy.permissions)), size):
            if len(candidates) >= max_candidates or spent >= transition_budget:
                cutoff.append("REPAIR_CANDIDATE_LIMIT" if len(candidates) >= max_candidates else "REPAIR_TRANSITION_LIMIT")
                status = "INCONCLUSIVE"
                break
            policy = ExplorationPolicy.model_validate({**bundle.policy.model_dump(mode="json"),
                "permissions": [rule.model_dump(mode="json") for index, rule in enumerate(bundle.policy.permissions) if index not in removed]})
            config = SearchConfig.model_validate({**bundle.config.model_dump(mode="json"),
                "max_transitions": min(bundle.config.max_transitions, transition_budget - spent)})
            candidate = create_exploration_bundle(bundle.model, policy, config)
            spent += candidate.report.transitions_examined
            summary = CandidateSummary.model_validate({
                **{key: getattr(candidate.report, key) for key in (
                    "decision", "property_status", "goal_status", "states_discovered",
                    "states_expanded", "transitions_examined", "transitions_enabled",
                    "frontier_exhausted", "cutoff_reasons", "coverage_issues", "policy_digest", "config_digest")},
                "report_digest": canonical_digest(candidate.report),
                "bundle_digest": candidate.integrity_digest,
            })
            candidates.append(RepairCandidate(removed_rule_indices=removed, report=summary))
            if candidate.report.decision == "INCONCLUSIVE":
                inconclusive_sizes.add(size)
            if candidate.report.decision == "VERIFIED":
                selected = candidate
                status = "FOUND"
                minimal = not any(previous < size for previous in inconclusive_sizes)
                break
        if selected or status == "INCONCLUSIVE":
            break
    if not selected and inconclusive_sizes:
        status = "INCONCLUSIVE"
        cutoff.append("INCOMPLETE_CANDIDATE_EXPLORATION")
    return RepairReport(status=status, original_bundle_digest=bundle.integrity_digest,
                        candidates=tuple(candidates), selected_bundle=selected,
                        max_removals=max_removals, max_candidates=max_candidates,
                        transition_budget=transition_budget, transitions_examined=spent,
                        minimality_proven=minimal, cutoff_reasons=tuple(dict.fromkeys(cutoff)))


def validate_repair_report(bundle: ExplorationBundle, report: RepairReport) -> RepairReport:
    """Bind a repair result to its baseline and replay its bounded search.

    Candidate summaries and selected bundles are evidence, not trusted inputs.
    Recomputing also verifies the reported minimum-cardinality claim and limits.
    """
    expected = search_repairs(bundle, max_removals=report.max_removals,
                              max_candidates=report.max_candidates,
                              transition_budget=report.transition_budget)
    if canonical_digest(report) != canonical_digest(expected):
        raise ValueError("repair report does not match deterministic reanalysis")
    return expected
