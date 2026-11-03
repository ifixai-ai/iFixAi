"""Execute only explicitly supplied local bindings, then independently observe.

Imported contracts never resolve Python names or execute tools. This is opt-in
implementation replay, not enforcement, isolation, or production authorization.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .integrity import canonical_digest, validate_exploration_bundle
from .models import ExplorationBundle, Witness

LIMITATIONS = (
    "Only explicitly supplied synchronous callables execute; imported names never dispatch tools.",
    "Independent observations compare declared Boolean facts, not unmodelled side effects or production causality.",
    "Witness costs are declared model units, not observed billing or real-money evidence.",
    "MATCHED means this local implementation matched this witness; it does not verify all runtime behavior.",
    "Exceptions and deferred returns may follow side effects; execution stops and cannot claim a match.",
    "All-zero digests denote evidence that could not be validated and are not evidence checksums.",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ExecutionContext(_Strict):
    actor_id: str = Field(min_length=1, max_length=64)
    action_id: str = Field(min_length=1, max_length=64)
    tool: str = Field(min_length=1, max_length=64)
    resource: str = Field(min_length=1, max_length=256)
    cost_units: StrictInt = Field(ge=0, le=2**63 - 1)


class ObservedStep(_Strict):
    index: StrictInt = Field(ge=1, le=64)
    action_id: str = Field(min_length=1, max_length=64)
    before: dict[str, StrictBool]
    after: dict[str, StrictBool] | None = None
    outcome: Literal["completed", "failed", "mismatch"]
    error_type: str | None = Field(default=None, max_length=100)


class ExecutionReport(_Strict):
    decision: Literal["MATCHED", "MODEL_MISMATCH", "EXECUTION_ERROR", "INCONCLUSIVE"]
    witness_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    steps: tuple[ObservedStep, ...] = Field(default=(), max_length=64)
    attempted_actions: StrictInt = Field(default=0, ge=0, le=64)
    completed_actions: StrictInt = Field(default=0, ge=0, le=64)
    initial_observed: dict[str, StrictBool] | None = None
    reason_codes: tuple[str, ...] = ()
    limitations: tuple[str, ...] = LIMITATIONS


def _sync_callable(function: Any) -> bool:
    return callable(function) and not any(check(function) or check(getattr(function, "__call__", None)) for check in (inspect.iscoroutinefunction, inspect.isasyncgenfunction, inspect.isgeneratorfunction))


def _immediate(value: Any) -> Any:
    if isinstance(value, Future) or inspect.isawaitable(value) or inspect.isgenerator(value) or inspect.isasyncgen(value):
        if inspect.iscoroutine(value) or inspect.isgenerator(value):
            value.close()
        raise TypeError("deferred callable results are not supported")
    return value


def _observation(function: Callable[[], dict[str, bool]], names: set[str]) -> dict[str, bool] | None:
    raw = _immediate(function())
    if not isinstance(raw, dict) or set(raw) != names or any(type(value) is not bool for value in raw.values()):
        return None
    return dict(raw)


def _error_type(error: Exception) -> str:
    name = type(error).__name__
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", name) else "Exception"


def execute_witness(bundle: ExplorationBundle, witness: Witness, bindings: dict[str, Callable[[ExecutionContext], Any]], reset: Callable[[], None], observe_state: Callable[[], dict[str, bool]], max_steps: int = 64) -> ExecutionReport:
    """Replay one retained witness against explicit bindings, stopping on mismatch.

    All evidence and binding declarations validate before reset or observation.
    No function timeout or background thread is created by this synchronous API.
    """
    bundle_digest = witness_digest = "0" * 64
    steps: list[ObservedStep] = []
    attempted = completed = 0
    initial: dict[str, bool] | None = None

    def result(decision, code):
        return ExecutionReport(decision=decision, witness_digest=witness_digest, bundle_digest=bundle_digest, steps=tuple(steps), attempted_actions=attempted, completed_actions=completed, initial_observed=initial, reason_codes=(code,))

    try:
        bundle = validate_exploration_bundle(bundle)
        witness = Witness.model_validate(witness.model_dump(mode="python", warnings=False))
        bundle_digest = canonical_digest(bundle)
        witness_digest = canonical_digest(witness)
    except Exception:  # noqa: BLE001 - malformed detached evidence must never trigger execution
        return result("INCONCLUSIVE", "INVALID_RETAINED_EVIDENCE")
    if witness not in (*bundle.report.safety_witnesses, *bundle.report.goal_witnesses):
        return result("INCONCLUSIVE", "WITNESS_NOT_RETAINED")
    if type(max_steps) is not int or not 1 <= max_steps <= 64 or len(witness.steps) > max_steps:
        return result("INCONCLUSIVE", "STEP_BUDGET_INVALID_OR_EXCEEDED")
    # Callbacks may retain and mutate the caller's mapping during replay. Admit
    # and execute one detached set of bindings, not later replacements.
    if isinstance(bindings, dict):
        bindings = dict(bindings)
    if not isinstance(bindings, dict) or not _sync_callable(reset) or not _sync_callable(observe_state) or any(step.action_id not in bindings or not _sync_callable(bindings[step.action_id]) for step in witness.steps):
        return result("INCONCLUSIVE", "MISSING_OR_DEFERRED_BINDING")
    names = set(bundle.model.initial_facts)
    try:
        # Runtime validation must inspect even a callable declared to return
        # None: user-supplied Python functions can violate that annotation.
        if _immediate(cast(Callable[[], Any], reset)()) is not None:
            return result("EXECUTION_ERROR", "RESET_MUST_RETURN_NONE")
        initial = _observation(observe_state, names)
    except Exception:  # noqa: BLE001 - return sanitized execution outcome, never raw exception payload
        return result("EXECUTION_ERROR", "RESET_OR_INITIAL_OBSERVER_ERROR")
    if initial is None or initial != witness.initial.facts:
        return result("MODEL_MISMATCH", "INITIAL_STATE_MISMATCH")
    for step in witness.steps:
        try:
            before = _observation(observe_state, names)
        except Exception:  # noqa: BLE001 - observer errors must not trigger later action calls
            return result("EXECUTION_ERROR", "BEFORE_OBSERVER_ERROR")
        if before is None or before != step.before.facts:
            return result("MODEL_MISMATCH", "BEFORE_STATE_MISMATCH")
        context = ExecutionContext(actor_id=step.actor_id, action_id=step.action_id, tool=step.tool, resource=step.resource, cost_units=step.cost_units)
        attempted += 1
        try:
            _immediate(bindings[step.action_id](context))
        except Exception as error:  # noqa: BLE001 - callable failure is retained without its message
            try:
                after = _observation(observe_state, names)
            except Exception:  # noqa: BLE001 - do not replace the application failure with observer failure
                after = None
            steps.append(ObservedStep(index=step.index, action_id=step.action_id, before=before, after=after, outcome="failed", error_type=_error_type(error)))
            return result("EXECUTION_ERROR", "ACTION_ERROR_OR_DEFERRED_RESULT")
        completed += 1
        try:
            after = _observation(observe_state, names)
        except Exception:  # noqa: BLE001 - action already occurred, report missing observations honestly
            steps.append(ObservedStep(index=step.index, action_id=step.action_id, before=before, after=None, outcome="failed", error_type="ObserverError"))
            return result("EXECUTION_ERROR", "AFTER_OBSERVER_ERROR")
        if after is None or after != step.after.facts:
            steps.append(ObservedStep(index=step.index, action_id=step.action_id, before=before, after=after, outcome="mismatch"))
            return result("MODEL_MISMATCH", "AFTER_STATE_MISMATCH")
        steps.append(ObservedStep(index=step.index, action_id=step.action_id, before=before, after=after, outcome="completed"))
    return result("MATCHED", "ALL_DECLARED_FACTS_MATCHED")
