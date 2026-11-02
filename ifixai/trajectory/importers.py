"""Bounded passive import of canonical captures and instrumented OTLP JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .models import Identifier, StrictModel, TraceCapture, TraceEvent, TraceId

MAX_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 100000


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON keys are not accepted")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite JSON values are not accepted")


def strict_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def read_input(path: str | Path) -> str:
    with Path(path).open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("input exceeds the 64 MiB limit")
    return data.decode("utf-8")


class CaptureHeader(StrictModel):
    schema_version: Literal["1"] = "1"
    trace_id: TraceId
    tenant_id: Identifier
    collector_id: Identifier
    capture_complete: bool = Field(strict=True)
    source_format: Literal["jsonl"] = "jsonl"


def load_jsonl_capture(path: str | Path) -> TraceCapture:
    # JSON strings may contain raw U+2028, U+2029 and U+0085; only newline separates rows.
    lines = [line for line in read_input(path).split("\n") if line.strip()]
    if not lines or len(lines) > MAX_EVENTS + 1:
        raise ValueError("JSONL requires a header and at most 100000 events")
    header = CaptureHeader.model_validate(strict_json(lines[0]))
    events = tuple(TraceEvent.model_validate(strict_json(line)) for line in lines[1:])
    return TraceCapture(**header.model_dump(), events=events)


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("OTLP envelope requires JSON objects")
    return value


def _array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError("OTLP envelope requires JSON arrays")
    return value


def _identifier(value: Any, length: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value) or int(value, 16) == 0:
        raise ValueError("OTLP requires nonzero hexadecimal trace and span IDs")
    return value.lower()


def _integer(value: Any) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        result = int(value)
    else:
        raise ValueError("OTLP timestamps and counts must be exact nonnegative integers")
    if not 0 <= result <= 2**63 - 1:
        raise ValueError("OTLP integer is out of range")
    return result


def _attributes(container: dict[str, Any], allowed: dict[str, str], warnings: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for entry in _array(container.get("attributes", [])):
        attribute = _object(entry)
        key = attribute.get("key")
        if not isinstance(key, str):
            raise TypeError("OTLP attribute keys must be strings")
        if not key.startswith("ifixai."):
            continue
        if key not in allowed:
            warnings.append("unknown reserved ifixai attribute")
            continue
        if key in result:
            raise ValueError("duplicate reserved OTLP attributes")
        value = _object(attribute.get("value"))
        variant = allowed[key]
        if set(value) != {variant}:
            raise ValueError("reserved OTLP attribute must have one correctly typed AnyValue")
        item = value[variant]
        if (variant == "stringValue" and not isinstance(item, str)) or (variant == "boolValue" and type(item) is not bool):
            raise ValueError("reserved OTLP attribute has the wrong value type")
        result[key] = item
    if _integer(container.get("droppedAttributesCount", 0)):
        warnings.append("OTLP attributes were dropped")
    return result


def load_otlp_capture(path: str | Path, *, trace_id: str | None = None) -> TraceCapture:
    """Ordinary span names never infer permissions, approvals or action outcomes."""
    raw = _object(strict_json(read_input(path)))
    spans: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    for resource_span in _array(raw.get("resourceSpans", [])):
        resource_span = _object(resource_span)
        resource = _object(resource_span.get("resource", {}))
        for scope_span in _array(resource_span.get("scopeSpans", [])):
            scope_span = _object(scope_span)
            for span in _array(scope_span.get("spans", [])):
                spans.append((_object(span), resource, scope_span))
                if len(spans) > MAX_EVENTS:
                    raise ValueError("OTLP exceeds the 100000 span limit")
    traces = {_identifier(span.get("traceId"), 32) for span, _, _ in spans}
    if trace_id is None:
        if len(traces) != 1:
            raise ValueError("OTLP requires one trace or explicit --trace-id selection")
        selected = next(iter(traces))
    else:
        selected = _identifier(trace_id, 32)
        if selected not in traces:
            raise ValueError("selected trace is absent from OTLP input")
    warnings: list[str] = []
    events: list[TraceEvent] = []
    metadata: list[dict[str, Any]] = []
    physical_parents: list[tuple[str, str]] = []
    for span, resource, scope_span in spans:
        if _identifier(span.get("traceId"), 32) != selected:
            continue
        # Scope attributes are not canonical event facts, but dropped metadata
        # or unknown reserved instrumentation still makes coverage uncertain.
        _attributes(_object(scope_span.get("scope", {})), {}, warnings)
        span_id = _identifier(span.get("spanId"), 16)
        if "kind" in span and (type(span["kind"]) is not int or not 0 <= span["kind"] <= 5):
            raise ValueError("OTLP span kind must be a numeric enum")
        if "status" in span:
            status = _object(span["status"])
            code = status.get("code", 0)
            if type(code) is not int or not 0 <= code <= 2:
                raise ValueError("OTLP status code must be a numeric enum")
        attrs = _attributes(resource, {"ifixai.tenant_id": "stringValue", "ifixai.collector_id": "stringValue", "ifixai.capture_complete": "boolValue"}, warnings)
        metadata.append(attrs)
        semantics = _attributes(span, {"ifixai.event": "stringValue"}, warnings)
        for count in ("droppedEventsCount", "droppedLinksCount"):
            if _integer(span.get(count, 0)):
                warnings.append("OTLP events or links were dropped")
        if "ifixai.event" not in semantics:
            warnings.append("uninstrumented span has no canonical event evidence")
            continue
        event = TraceEvent.model_validate(strict_json(semantics["ifixai.event"]))
        if "startTimeUnixNano" not in span or "endTimeUnixNano" not in span:
            warnings.append("OTLP span timestamp metadata is incomplete")
        timestamp = _integer(span.get("endTimeUnixNano", span.get("startTimeUnixNano")))
        if "startTimeUnixNano" in span and _integer(span["startTimeUnixNano"]) > timestamp:
            raise ValueError("OTLP span ends before its start")
        if event.trace_id != selected or event.event_id != span_id or event.time_ns != timestamp:
            raise ValueError("canonical event is not bound to its OTLP span metadata")
        parent = span.get("parentSpanId")
        if parent:
            physical_parents.append((span_id, _identifier(parent, 16)))
        elif event.parents:
            warnings.append("OTLP parent metadata is missing for a dependent event")
        events.append(event)
    required = {"ifixai.tenant_id", "ifixai.collector_id", "ifixai.capture_complete"}
    complete_metadata = [item for item in metadata if set(item) == required]
    if len(complete_metadata) != len(metadata):
        warnings.append("OTLP resource lacks explicit capture metadata")
    first = complete_metadata[0] if complete_metadata else {}
    if any(item != first for item in metadata):
        warnings.append("OTLP resource capture metadata is inconsistent")
    tenant = first.get("ifixai.tenant_id") or (events[0].tenant_id if events else "unknown")
    collector = first.get("ifixai.collector_id") or "unidentified-otlp-collector"
    if any(event.tenant_id != tenant for event in events):
        warnings.append("OTLP event tenant differs from resource metadata")
    by_id = {event.event_id: event for event in events}
    for event_id, parent_id in physical_parents:
        if parent_id not in by_id or parent_id not in by_id[event_id].parents:
            warnings.append("OTLP parent is absent from canonical causal evidence")
    return TraceCapture(trace_id=selected, tenant_id=tenant, collector_id=collector,
                        events=tuple(sorted(events, key=lambda event: event.sequence)),
                        capture_complete=first.get("ifixai.capture_complete", False) is True,
                        import_warnings=tuple(dict.fromkeys(warnings)), source_format="otlp")
