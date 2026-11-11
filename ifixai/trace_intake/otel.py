"""OpenTelemetry OTLP-JSON export, read through the GenAI semantic conventions.

Mapping (by `gen_ai.operation.name`):
  execute_tool                          -> action (gen_ai.tool.name, gen_ai.tool.call.arguments)
  chat / text_completion / generate_content -> output (final text of gen_ai.output.messages,
                                           gen_ai.completion or gen_ai.completion.0.content)
  retrieval                             -> source (gen_ai.retrieval.documents)
  invoke_agent / create_agent           -> structural; names the agent for its descendants
  embeddings                            -> structural
Outcome comes only from the span status (ERROR -> failed, OK -> completed) or `error.type`;
an unset status is `unknown`. A span carrying `ifixai.event` contributes that canonical event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ifixai.trace_intake._core import MAX_EVENTS, TraceIntakeError, Warnings
from ifixai.trace_intake.builder import MISSING, ParsedTrace, Step, select_trace
from ifixai.trace_intake.text import documents_from, final_text, maybe_json

STATUS_NAMES = {"STATUS_CODE_UNSET": 0, "STATUS_CODE_OK": 1, "STATUS_CODE_ERROR": 2}
KNOWN_RESERVED = {"ifixai.event", "ifixai.actor_id", "ifixai.capture_complete"}
OUTPUT_OPERATIONS = {"chat", "text_completion", "generate_content"}
STRUCTURAL_OPERATIONS = {"invoke_agent", "create_agent", "embeddings"}


def require_object(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TraceIntakeError(f"OTLP {what} must be a JSON object")
    return value


def require_array(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise TraceIntakeError(f"OTLP {what} must be a JSON array")
    return value


def hex_id(value: Any, length: int) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value)
        or int(value, 16) == 0
    ):
        raise TraceIntakeError("OTLP requires nonzero hexadecimal trace and span ids")
    return value.lower()


def otlp_integer(value: Any) -> int:
    if type(value) is int:
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        result = int(value)
    else:
        raise TraceIntakeError("OTLP timestamps and counts must be exact nonnegative integers")
    if not 0 <= result <= 2**63 - 1:
        raise TraceIntakeError("OTLP integer is out of range")
    return result


def any_value(value: Any, depth: int = 0) -> Any:
    """An OTLP AnyValue as a plain JSON value."""
    if depth > 16 or not isinstance(value, dict) or len(value) != 1:
        raise TraceIntakeError("OTLP attribute values must be one AnyValue")
    (variant, item), = value.items()
    if variant in ("stringValue", "boolValue", "doubleValue", "bytesValue"):
        return item
    if variant == "intValue":
        return int(item) if isinstance(item, (int, str)) and str(item).lstrip("-").isdigit() else item
    if variant == "arrayValue":
        return [any_value(entry, depth + 1) for entry in require_array(
            require_object(item, "arrayValue").get("values", []), "arrayValue.values")]
    if variant == "kvlistValue":
        return attributes(require_object(item, "kvlistValue").get("values", []), depth + 1)
    raise TraceIntakeError(f"unknown OTLP AnyValue variant {variant!r}")


def attributes(entries: Any, depth: int = 0) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for entry in require_array(entries, "attributes"):
        entry = require_object(entry, "attribute")
        key = entry.get("key")
        if not isinstance(key, str):
            raise TraceIntakeError("OTLP attribute keys must be strings")
        if key in result:
            raise TraceIntakeError("duplicate OTLP attribute keys")
        result[key] = any_value(entry.get("value"), depth)
    return result


@dataclass
class OtlpSpan:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_ns: int | None
    end_ns: int | None
    status: int
    attrs: dict[str, Any]
    resource: dict[str, Any]


def status_code(span: dict[str, Any]) -> int:
    code = require_object(span.get("status", {}), "status").get("code", 0)
    code = STATUS_NAMES.get(code, code) if isinstance(code, str) else code
    if type(code) is not int or not 0 <= code <= 2:
        raise TraceIntakeError("OTLP status code must be a status enum")
    return code


def collect_spans(raw: Any, warnings: Warnings) -> list[OtlpSpan]:
    """Every span in an OTLP-JSON export, refusing more than MAX_EVENTS."""
    raw = require_object(raw, "export")
    spans: list[OtlpSpan] = []
    for resource_span in require_array(raw.get("resourceSpans", []), "resourceSpans"):
        resource_span = require_object(resource_span, "resourceSpan")
        resource_obj = require_object(resource_span.get("resource", {}), "resource")
        resource = attributes(resource_obj.get("attributes", []))
        if otlp_integer(resource_obj.get("droppedAttributesCount", 0)):
            warnings.add("dropped_attributes", "OTLP resource attributes were dropped")
        for scope_span in require_array(resource_span.get("scopeSpans", []), "scopeSpans"):
            scope_span = require_object(scope_span, "scopeSpan")
            for span in require_array(scope_span.get("spans", []), "spans"):
                spans.append(read_span(require_object(span, "span"), resource, warnings))
                if len(spans) > MAX_EVENTS:
                    raise TraceIntakeError(f"OTLP exceeds the {MAX_EVENTS} span limit")
    return spans


def read_span(span: dict[str, Any], resource: dict[str, Any], warnings: Warnings) -> OtlpSpan:
    span_id = hex_id(span.get("spanId"), 16)
    for count in ("droppedAttributesCount", "droppedEventsCount", "droppedLinksCount"):
        if otlp_integer(span.get(count, 0)):
            warnings.add("dropped_attributes", "OTLP span attributes, events or links were dropped",
                         span_id)
    parent = span.get("parentSpanId")
    start = span.get("startTimeUnixNano")
    end = span.get("endTimeUnixNano")
    name = span.get("name", "")
    return OtlpSpan(
        trace_id=hex_id(span.get("traceId"), 32),
        span_id=span_id,
        parent_id=hex_id(parent, 16) if parent else None,
        name=name if isinstance(name, str) else "",
        start_ns=otlp_integer(start) if start is not None else None,
        end_ns=otlp_integer(end) if end is not None else None,
        status=status_code(span),
        attrs=attributes(span.get("attributes", [])),
        resource=resource,
    )


def group_spans(spans: list[OtlpSpan], trace_id: str | None, source: str) -> list[OtlpSpan]:
    groups: dict[str, list[OtlpSpan]] = {}
    for span in spans:
        groups.setdefault(span.trace_id, []).append(span)
    if trace_id is not None:
        trace_id = trace_id.strip().lower()
    return groups[select_trace(groups, trace_id, source)]


def declared_complete(spans: list[OtlpSpan], warnings: Warnings) -> bool:
    """True only if every resource of the trace explicitly declares the capture complete."""
    values = [span.resource.get("ifixai.capture_complete") for span in spans]
    if any(value is not None and type(value) is not bool for value in values):
        warnings.add("capture_complete_invalid", "ifixai.capture_complete must be a boolean")
        return False
    return bool(values) and all(value is True for value in values)


def reserved_warnings(attrs: dict[str, Any], warnings: Warnings, where: str) -> None:
    for key in attrs:
        if key.startswith("ifixai.") and key not in KNOWN_RESERVED:
            warnings.add("unknown_reserved_attribute", "unknown reserved ifixai attribute", where)


def outcome(span: OtlpSpan) -> str:
    if span.status == 2 or span.attrs.get("error.type"):
        return "failed"
    return "completed" if span.status == 1 else "unknown"


def output_text(attrs: dict[str, Any]) -> str | None:
    for key in ("gen_ai.output.messages", "gen_ai.completion", "gen_ai.completion.0.content"):
        if key in attrs:
            text = final_text(maybe_json(attrs[key]))
            if text:
                return text
    return None


def genai_step(span: OtlpSpan, warnings: Warnings) -> Step:
    attrs = span.attrs
    reserved_warnings(attrs, warnings, span.span_id)
    step = Step(
        step_id=span.span_id,
        parent_id=span.parent_id,
        start_ns=span.start_ns,
        end_ns=span.end_ns,
        actor_id=attrs.get("ifixai.actor_id") or attrs.get("gen_ai.agent.id")
        or attrs.get("gen_ai.agent.name"),
        explicit=attrs.get("ifixai.event"),
    )
    operation = attrs.get("gen_ai.operation.name")
    if operation == "execute_tool":
        step.kind = "action"
        step.action = attrs.get("gen_ai.tool.name")
        step.arguments = attrs.get("gen_ai.tool.call.arguments", MISSING)
        step.outcome = outcome(span)
    elif operation in OUTPUT_OPERATIONS:
        step.kind = "output"
        step.text = output_text(attrs)
    elif operation == "retrieval":
        step.kind = "source"
        step.documents = documents_from(attrs.get("gen_ai.retrieval.documents"))
        data_source = attrs.get("gen_ai.data_source.id")
        step.retrieval_resource = f"retrieval:{data_source}" if data_source else None
    elif operation in STRUCTURAL_OPERATIONS:
        if operation != "embeddings":
            step.agent = step.actor_id
    elif operation is None:
        if step.explicit is None:
            warnings.add(
                "span_without_genai_semantics",
                "span has no gen_ai.operation.name; it is not mapped", span.span_id,
            )
    else:
        warnings.add(
            "unknown_span_kind", f"GenAI operation {str(operation)[:40]!r} is not mapped",
            span.span_id,
        )
    return step


def parse(raw: Any, trace_id: str | None, warnings: Warnings) -> ParsedTrace:
    spans = group_spans(collect_spans(raw, warnings), trace_id, "otel")
    roots = [span for span in spans if span.parent_id is None]
    if len(roots) != 1:
        warnings.add("root_span_count", f"trace has {len(roots)} root spans, not one")
    root = roots[0] if roots else None
    return ParsedTrace(
        source="otel",
        raw_trace_id=spans[0].trace_id,
        steps=[genai_step(span, warnings) for span in spans],
        label=root.name if root else "",
        declared_complete=declared_complete(spans, warnings),
        root_start_ns=root.start_ns if root else None,
        root_end_ns=root.end_ns if root else None,
    )
