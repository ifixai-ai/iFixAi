"""Shared evidence builders and fixture gate for control-plane *structural* probe inspections.

A structural inspection hands the deployment's control plane a runner-authored request and scores the
decision it returns — no conversational reply, no SUT prompt. Every such runner needs the same small
set of type-agnostic pieces: a fixture gate over declared requirements, a pre-run shortfall sentinel,
a capability-missing sentinel, and CI de-inflation for replicated payloads. They live here once so a
fix lands in one place; each runner keeps its own probe types and domain builders.

No I/O, no module state: every function is pure over its arguments.
"""

from collections.abc import Callable

from ifixai.core.types import (
    EvaluationMethod,
    EvidenceItem,
    Fixture,
    InspectionMethod,
)
from ifixai.rules.loader import RuleLoadError


def validate_structural_fixture(
    plan: object,
    fixture: Fixture,
    validators: dict[str, Callable[[Fixture], bool]],
    test_id: str,
) -> None:
    """Gate the run on the plan's declared `fixture_requirements` (plus a mandatory non-empty
    `users`) using explicit validators — no getattr, per the repo style rule. Raises
    `RuleLoadError`, which the harness maps to `TestStatus.ERROR` with a populated `error_message`.
    """
    requirements = ["users", *plan.fixture_requirements]  # type: ignore[attr-defined]
    missing: list[str] = []
    for name in requirements:
        validator = validators.get(name)
        if validator is None or not validator(fixture):
            missing.append(name)
    if missing:
        raise RuleLoadError(
            f"{test_id} fixture missing or empty required sections: {sorted(set(missing))}. "
            f"definition.yaml declares fixture_requirements={plan.fixture_requirements}; "  # type: ignore[attr-defined]
            f"{test_id} also requires non-empty users."
        )


def insufficient_evidence_item(
    sentinel_id: str,
    predicted: int,
    minimum: int,
    actual: str,
    noun: str = "scored probes",
) -> EvidenceItem:
    """Pre-run shortfall sentinel: too few scored items for a meaningful rate.
    Short-circuited before any provider call. Routes to INCONCLUSIVE.

    The runner supplies the `actual` line (which names its probe arithmetic) and its own
    `sentinel_id`.
    """
    return EvidenceItem(
        test_case_id=sentinel_id,
        description=(
            f"Predicted {noun} are below the floor for a statistically meaningful "
            "rate — short-circuited before any provider call."
        ),
        expected=f"predicted {noun} >= {minimum}",
        actual=actual,
        passed=False,
        inspection_method=InspectionMethod.STRUCTURAL,
        evaluation_method=EvaluationMethod.STRUCTURAL,
        details={"predicted_evidence": predicted, "min_evidence_items": minimum},
    )


def capability_missing_item(
    sentinel_id: str,
    test_id: str,
    hook_name: str,
    flag_name: str,
) -> EvidenceItem:
    """Single diagnostic when the provider exposes no structural hook for this inspection.

    Without it the runner returns `[]` and the harness produces a silent INCONCLUSIVE/FAIL with no
    evidence. Excluded from `compute_score`; one item < `min_evidence_items` routes to INCONCLUSIVE
    (capability-missing, not a measured failure). `hook_name` is the provider method, `flag_name`
    the capability flag.
    """
    return EvidenceItem(
        test_case_id=sentinel_id,
        description=(
            f"Provider exposes no {hook_name} capability — {test_id} cannot be evaluated"
        ),
        expected=f"{flag_name}=True",
        actual=f"{flag_name}=False",
        passed=False,
        inspection_method=InspectionMethod.STRUCTURAL,
        evaluation_method=EvaluationMethod.STRUCTURAL,
        details={f"capability_{flag_name.removeprefix('has_')}": False},
    )


def users_present(fixture: Fixture) -> bool:
    """The one fixture validator every structural runner needs: non-empty users."""
    return bool(fixture.users)


def annotate_replication(
    evidence: list[EvidenceItem],
    payload_keys: list[str],
) -> list[EvidenceItem]:
    """Mark replicated structural evidence for CI de-inflation.

    A structural runner that drives a (user x probe) cross-product repeats the same provider payload
    once per fixture user whenever the user never enters it, so the raw item count overstates the
    independent-evidence count. This attaches `details["n_effective"] = 1` to the first valid (no
    extraction error) item per distinct payload key and `0` to its replicas and to trailing
    diagnostics, so `compute_test_ci`'s Wilson denominator equals the distinct-payload count. Point
    scores are unchanged — only the CI narrows or widens honestly.

    `payload_keys` aligns 1:1 with the leading probe-item entries of `evidence`; any evidence beyond
    `len(payload_keys)` is a diagnostic and is annotated `n_effective=0`.
    """
    if len(payload_keys) > len(evidence):
        raise ValueError(
            f"payload_keys ({len(payload_keys)}) exceeds evidence ({len(evidence)})"
        )
    seen: set[str] = set()
    annotated: list[EvidenceItem] = []
    for index, item in enumerate(evidence):
        weight = 0
        if index < len(payload_keys) and item.extraction_error is None:
            key = payload_keys[index]
            if key not in seen:
                seen.add(key)
                weight = 1
        annotated.append(
            item.model_copy(
                update={"details": {**item.details, "n_effective": weight}}
            )
        )
    return annotated
