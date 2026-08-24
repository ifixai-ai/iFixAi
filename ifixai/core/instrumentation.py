"""Opt-in provider-call tracing, for measuring where a run's wall time goes.

Disabled unless ``IFIXAI_TRACE_CALLS=1``. When disabled every hook below is a
context-variable read and an immediate yield: no timestamps, no allocation, no
list growth. When enabled each SUT and judge call contributes one
:class:`CallRecord` to a run-scoped log that `ifixai.cli.perf_report` renders.

The log is a single list bound to a ``ContextVar`` before the inspection tasks
fan out. Child tasks inherit a snapshot of the context, and a snapshot copies the
*reference*, so every task appends to the same list without any lock — appends to
a list are atomic under the GIL and the run's tasks share one event loop thread.
"""

import json
import os
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Literal, TypedDict

TRACE_ENV_VAR = "IFIXAI_TRACE_CALLS"
TRACE_OUTPUT_ENV_VAR = "IFIXAI_TRACE_OUTPUT"
DEFAULT_TRACE_PATH = "ifixai-trace.json"

CallRole = Literal["sut", "judge"]


class CallRecord(TypedDict):
    """One completed provider call, timed at the call site."""

    inspection_id: str
    role: CallRole
    started_at: float
    ended_at: float
    duration_seconds: float
    failed: bool


# Stands in for the inspection id outside a governed run — a direct `run_single`,
# a unit test. Callers that key policy off the active inspection must treat it as
# "unknown", never as one more inspection.
UNATTRIBUTED_INSPECTION = "<unattributed>"

# The inspection whose task tree is making the call. Set by the core runner as
# each inspection starts; child tasks inherit it, so call sites need not thread
# the id through the harness.
ACTIVE_INSPECTION: ContextVar[str] = ContextVar(
    "ifixai_active_inspection", default=UNATTRIBUTED_INSPECTION
)

# The run's call log, or None when tracing is off. None (not an empty list) is
# the disabled signal so the hot path is one identity check.
CALL_LOG: ContextVar[list[CallRecord] | None] = ContextVar(
    "ifixai_call_log", default=None
)


def is_tracing_enabled() -> bool:
    """True when the operator asked for a traced run via the environment."""
    return os.environ.get(TRACE_ENV_VAR) == "1"


def start_trace() -> list[CallRecord] | None:
    """Bind a fresh call log to this context and return it, or None when off.

    Call once per run, before inspections fan out. The returned list is the same
    object the call sites append to, so the caller can read it after the run.
    """
    if not is_tracing_enabled():
        CALL_LOG.set(None)
        return None
    log: list[CallRecord] = []
    CALL_LOG.set(log)
    return log


def current_trace() -> list[CallRecord] | None:
    """This context's call log, or None when tracing is off."""
    return CALL_LOG.get()


def trace_output_path() -> Path:
    """Where a traced run writes its log; overridable via the environment."""
    return Path(os.environ.get(TRACE_OUTPUT_ENV_VAR) or DEFAULT_TRACE_PATH)


def dump_trace() -> Path | None:
    """Write this context's call log as JSON and return the path.

    Returns None when tracing is off or the run recorded nothing, so callers can
    stay silent rather than emit an empty artifact.
    """
    log = CALL_LOG.get()
    if not log:
        return None
    path = trace_output_path()
    path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    return path


def load_trace(path: Path) -> list[CallRecord]:
    """Read a trace written by :func:`dump_trace`."""
    return json.loads(path.read_text(encoding="utf-8"))


def set_active_inspection(test_id: str) -> None:
    """Attribute subsequent provider calls on this task to ``test_id``."""
    ACTIVE_INSPECTION.set(test_id)


@asynccontextmanager
async def trace_call(role: CallRole):
    """Time one provider call into the run's log; a no-op when tracing is off.

    Records the call even when it raises, flagged ``failed``, so retry storms and
    timeout tails show up in the report instead of vanishing.
    """
    log = CALL_LOG.get()
    if log is None:
        yield
        return
    inspection_id = ACTIVE_INSPECTION.get()
    # perf_counter, not monotonic: on Windows monotonic ticks at ~15.6ms, which
    # rounds sub-tick calls to a 0.0 duration and silently understates busy time.
    started_at = time.perf_counter()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        ended_at = time.perf_counter()
        log.append(
            CallRecord(
                inspection_id=inspection_id,
                role=role,
                started_at=started_at,
                ended_at=ended_at,
                duration_seconds=ended_at - started_at,
                failed=failed,
            )
        )
