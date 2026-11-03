"""Passive observation of supplied Python callables; never a policy enforcer.

Argument dictionaries and costs are caller declarations, not authenticated billing
or proof that those arguments describe the callable. Imported names are never run.
"""

from __future__ import annotations

import inspect
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from .integrity import canonical_digest
from .models import ActionPayload, EndPayload, StartPayload, TraceCapture, TraceEvent

_T = TypeVar("_T")


def _json_object_keys(arguments: dict[str, Any]) -> None:
    pending: list[Any] = [arguments]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, (dict, list, tuple)):
            if id(value) in seen:
                continue
            seen.add(id(value))
            if isinstance(value, dict):
                if any(not isinstance(key, str) for key in value):
                    raise TypeError("all semantic argument object keys must be strings")
                pending.extend(value.values())
            else:
                pending.extend(value)


def _synchronous_result(value: _T) -> _T:
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError("invoke does not await callable return values")
    if inspect.isgenerator(value) or inspect.isasyncgen(value):
        if inspect.isgenerator(value):
            value.close()
        raise TypeError("invoke does not consume deferred generator return values")
    return value


class TraceRecorder:
    """Thread-safe local recorder with explicit sealing and no raw arguments.

    Unspecified parents follow recorder program order; explicit parents are
    retained verbatim. This records local ordering, not distributed causality.
    Failures prevent a complete declaration because side effects may precede them.
    """

    def __init__(self, tenant_id: str, collector_id: str, label: str = "", trace_id: str | None = None, *, clock_ns: Callable[[], int] = time.time_ns):
        self._lock = threading.RLock()
        self._clock = clock_ns
        self._events: list[TraceEvent] = []
        self._active = 0
        self._failed = False
        self._sealed = False
        self._trace_id = trace_id or secrets.token_hex(16)
        self._tenant_id = tenant_id
        self._collector_id = collector_id
        # Validate header fields before recording even the first observed fact.
        TraceCapture(trace_id=self._trace_id, tenant_id=tenant_id, collector_id=collector_id, events=(), capture_complete=False)
        self.emit(StartPayload(label=label))

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def _time(self) -> int:
        observed = self._clock()
        if isinstance(observed, bool) or not isinstance(observed, int) or not 0 <= observed <= 2**63 - 1:
            raise ValueError("clock_ns must return a nonnegative bounded integer")
        return max(observed, self._events[-1].time_ns if self._events else 0)

    def emit(self, payload: Any, parents: tuple[str, ...] = ()) -> TraceEvent:
        """Retain a typed payload; semantic correctness is evaluated separately."""
        return self._emit(payload, parents)

    def _emit(self, payload: Any, parents: tuple[str, ...], *, reserved: bool = False) -> TraceEvent:
        with self._lock:
            if self._sealed:
                raise RuntimeError("recording is already sealed")
            if len(self._events) + self._active - int(reserved) >= 99999:
                raise ValueError("event bound reached; reserve pending calls and the final end event")
            links = parents or ((self._events[-1].event_id,) if self._events else ())
            event = TraceEvent(event_id=secrets.token_hex(8), trace_id=self._trace_id, tenant_id=self._tenant_id, sequence=len(self._events) + 1, time_ns=self._time(), parents=links, payload=payload)
            if event.payload.kind == "end":
                raise ValueError("use seal() to create the terminal end record")
            if event.payload.kind == "start" and self._events:
                raise ValueError("recorder already has its start record")
            self._events.append(event)
            return event

    def invoke(self, function: Callable[..., _T], *args: Any, actor_id: str, action: str, resource: str, arguments: dict[str, Any], approval_id: str | None = None, delegation_id: str | None = None, parents: tuple[str, ...] = (), cost_units: int = 0, **kwargs: Any) -> _T:
        """Execute this supplied callable once and retain its observed outcome.

        Exceptions propagate unchanged. Async callables require application-level
        instrumentation; rejecting them before execution avoids recording an
        unawaited coroutine as a completed action.
        """
        if not callable(function) or any(check(function) or check(getattr(function, "__call__", None)) for check in (inspect.iscoroutinefunction, inspect.isasyncgenfunction, inspect.isgeneratorfunction)):
            raise TypeError("invoke requires a synchronous supplied callable")
        if not isinstance(arguments, dict) or any(not isinstance(key, str) for key in arguments):
            raise TypeError("arguments must be a JSON object with string keys")
        _json_object_keys(arguments)
        digest = canonical_digest(arguments)
        template = ActionPayload(actor_id=actor_id, action=action, resource=resource, arguments_digest=digest, approval_id=approval_id, delegation_id=delegation_id, cost_units=cost_units)
        with self._lock:
            if self._sealed:
                raise RuntimeError("recording is already sealed")
            if len(self._events) + self._active >= 99999:
                raise ValueError("event bound reached; reserve the final end event")
            # Validate requested links before any callable side effect.
            links = parents or (self._events[-1].event_id,)
            TraceEvent(event_id="0" * 16, trace_id=self._trace_id, tenant_id=self._tenant_id, sequence=len(self._events) + 1, time_ns=self._events[-1].time_ns, parents=links, payload=template)
            self._active += 1
        try:
            value = _synchronous_result(function(*args, **kwargs))
        except BaseException:
            with self._lock:
                self._failed = True
                try:
                    self._emit(template.model_copy(update={"outcome": "failed"}), links, reserved=True)
                except Exception:  # noqa: BLE001 - preserve the original application exception
                    # Preserve the original application exception; capture remains
                    # explicitly incomplete if recording itself also fails.
                    self._failed = True
            raise
        else:
            with self._lock:
                try:
                    self._emit(template, links, reserved=True)
                except Exception:
                    self._failed = True
                    raise
            return value
        finally:
            with self._lock:
                self._active -= 1

    def seal(self, *, complete: bool = True) -> TraceCapture:
        """Finish a closed recording; completeness is only a local declaration."""
        with self._lock:
            if self._sealed:
                raise RuntimeError("recording is already sealed")
            if not isinstance(complete, bool):
                raise TypeError("complete must be a boolean")
            if self._active:
                raise RuntimeError("cannot seal while callable execution is pending")
            complete = complete and not self._failed
            end = TraceEvent(event_id=secrets.token_hex(8), trace_id=self._trace_id, tenant_id=self._tenant_id, sequence=len(self._events) + 1, time_ns=self._time(), parents=(self._events[-1].event_id,), payload=EndPayload(complete=complete, declared_events=len(self._events) + 1))
            self._events.append(end)
            self._sealed = True
            return TraceCapture(trace_id=self._trace_id, tenant_id=self._tenant_id, collector_id=self._collector_id, events=tuple(self._events), capture_complete=complete, source_format="native")

    def capture(self, *, complete: bool = True) -> TraceCapture:
        return self.seal(complete=complete)

    def write_jsonl(self, path: str | Path, *, complete: bool = True) -> TraceCapture:
        """Seal and write a new owner-only file; refuse any existing destination."""
        capture = self.seal(complete=complete)
        write_capture_jsonl(capture, path)
        return capture


def write_capture_jsonl(capture: TraceCapture, path: str | Path) -> None:
    """Atomic exclusive publication of a native recording's strict JSONL rows."""
    capture = TraceCapture.model_validate(capture.model_dump(mode="json"))
    if capture.source_format != "native" or capture.import_warnings:
        raise ValueError("JSONL recorder export accepts only native captures without import warnings")
    from .importers import MAX_BYTES

    destination = Path(path)
    header = capture.model_dump(mode="json", exclude={"events", "import_warnings"})
    header["source_format"] = "jsonl"
    data = "\n".join([json.dumps(header, ensure_ascii=False), *(event.model_dump_json() for event in capture.events)]) + "\n"
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise ValueError("capture exceeds the JSONL import byte limit")
    temporary = destination.with_name(destination.name + "." + secrets.token_hex(8) + ".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link creation is atomic and fails if the destination exists.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
