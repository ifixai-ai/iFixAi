"""Arize Phoenix / OpenInference spans (Phoenix JSON span export, or OTLP with OpenInference).

Mapping (by `openinference.span.kind`):
  TOOL      -> action (tool.name, tool_call.function.arguments or input.value)
  LLM       -> output (last assistant llm.output_messages content, else output.value)
  RETRIEVER -> source (retrieval.documents.N.document.id / content)
  AGENT     -> structural; names the agent for its descendants
  CHAIN, EMBEDDING, RERANKER, GUARDRAIL, EVALUATOR, PROMPT -> structural
Outcome: status ERROR -> failed, OK -> completed, UNSET -> unknown.
"""

from __future__ import annotations

from typing import Any

from ifixai.trace_intake import otel
from ifixai.trace_intake._core import MAX_EVENTS, TraceIntakeError, Warnings, iso_to_ns
from ifixai.trace_intake.builder import (
    MISSING,
    Document,
    ParsedTrace,
    Step,
    select_trace,
)
from ifixai.trace_intake.text import final_text

STRUCTURAL = {"CHAIN", "EMBEDDING", "RERANKER", "GUARDRAIL", "EVALUATOR", "PROMPT", "AGENT"}
STATUS = {"OK": 1, "ERROR": 2, "UNSET": 0}


def flatten(value: Any, prefix: str = "", out: dict[str, Any] | None = None,
            depth: int = 0) -> dict[str, Any]:
    """Nested attribute objects and lists of objects as OpenInference's dotted keys."""
    out = {} if out is None else out
    if depth > 16:
        raise TraceIntakeError("attributes nest too deeply")
    if isinstance(value, dict) and value:
        for key, child in value.items():
            flatten(child, f"{prefix}{key}.", out, depth + 1)
    elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        for index, child in enumerate(value):
            flatten(child, f"{prefix}{index}.", out, depth + 1)
    elif prefix:
        out[prefix[:-1]] = value
    return out


def indexed(attrs: dict[str, Any], prefix: str) -> dict[int, dict[str, Any]]:
    items: dict[int, dict[str, Any]] = {}
    for key, value in attrs.items():
        if key.startswith(prefix):
            head, _, rest = key[len(prefix):].partition(".")
            if head.isdigit():
                items.setdefault(int(head), {})[rest] = value
    return dict(sorted(items.items()))


def llm_text(attrs: dict[str, Any]) -> str | None:
    messages = indexed(attrs, "llm.output_messages.")
    for fields in reversed(list(messages.values())):
        if fields.get("message.role", "assistant") in ("assistant", "ai", "model"):
            content = fields.get("message.content")
            if isinstance(content, str) and content:
                return content
    return final_text(attrs.get("output.value"))


def span_step(span_id: str, parent: str | None, name: str, start: int | None,
              end: int | None, status: int, attrs: dict[str, Any], warnings: Warnings) -> Step:
    otel.reserved_warnings(attrs, warnings, span_id)
    kind = str(attrs.get("openinference.span.kind") or "").upper()
    step = Step(
        step_id=span_id, parent_id=parent, start_ns=start, end_ns=end,
        actor_id=attrs.get("ifixai.actor_id") or attrs.get("agent.name"),
        explicit=attrs.get("ifixai.event"),
    )
    if kind == "TOOL":
        step.kind = "action"
        step.action = attrs.get("tool.name") or name or None
        if "tool_call.function.arguments" in attrs:
            step.arguments = attrs["tool_call.function.arguments"]
        else:
            step.arguments = attrs.get("input.value", MISSING)
        step.outcome = {2: "failed", 1: "completed"}.get(status, "unknown")
    elif kind == "LLM":
        step.kind = "output"
        step.text = llm_text(attrs)
    elif kind == "RETRIEVER":
        step.kind = "source"
        step.documents = [
            Document(fields.get("document.id"), fields.get("document.content"))
            for fields in indexed(attrs, "retrieval.documents.").values()
        ]
    elif kind in STRUCTURAL:
        if kind == "AGENT":
            step.agent = attrs.get("agent.name") or name or None
    elif step.explicit is None:
        warnings.add("unknown_span_kind",
                     f"OpenInference span kind {kind[:40] or 'missing'!r} is not mapped", span_id)
    return step


def phoenix_spans(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("spans", raw.get("data", [raw]))
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise TraceIntakeError("Phoenix export must be a list of span objects")
    if len(raw) > MAX_EVENTS:
        raise TraceIntakeError(f"Phoenix export exceeds the {MAX_EVENTS} span limit")
    return raw


def parse_phoenix(raw: Any, trace_id: str | None, warnings: Warnings) -> ParsedTrace:
    groups: dict[str, list[dict[str, Any]]] = {}
    for span in phoenix_spans(raw):
        context = span.get("context") if isinstance(span.get("context"), dict) else {}
        if not context.get("trace_id") or not context.get("span_id"):
            raise TraceIntakeError("every Phoenix span needs context.trace_id and span_id")
        groups.setdefault(str(context["trace_id"]), []).append(span)
    selected = select_trace(groups, trace_id, "arize")
    steps, root = [], None
    for span in groups[selected]:
        span_id = str(span["context"]["span_id"])
        attrs = flatten(span.get("attributes") or {})
        if "openinference.span.kind" not in attrs and span.get("span_kind"):
            attrs["openinference.span.kind"] = span["span_kind"]
        times = []
        for key in ("start_time", "end_time"):
            moment = iso_to_ns(span.get(key)) if span.get(key) is not None else None
            if span.get(key) is not None and moment is None:
                warnings.add("timestamp_unparseable", "timestamp is not ISO-8601; ignored", span_id)
            times.append(moment)
        status = STATUS.get(str(span.get("status_code") or "UNSET").upper(), 0)
        parent = span.get("parent_id")
        step = span_step(span_id, str(parent) if parent else None, str(span.get("name") or ""),
                         times[0], times[1], status, attrs, warnings)
        steps.append(step)
        if not parent:
            root = root or (span, step, attrs)
    roots = [s for s in steps if s.parent_id is None]
    if len(roots) != 1:
        warnings.add("root_span_count", f"trace has {len(roots)} root spans, not one")
    return ParsedTrace(
        source="arize",
        raw_trace_id=selected,
        steps=steps,
        label=str(root[0].get("name") or "") if root else "",
        declared_complete=bool(root) and root[2].get("ifixai.capture_complete") is True,
        root_start_ns=root[1].start_ns if root else None,
        root_end_ns=root[1].end_ns if root else None,
    )


def parse_otlp(raw: Any, trace_id: str | None, warnings: Warnings) -> ParsedTrace:
    spans = otel.group_spans(otel.collect_spans(raw, warnings), trace_id, "arize")
    steps = [span_step(s.span_id, s.parent_id, s.name, s.start_ns, s.end_ns, s.status,
                       s.attrs, warnings) for s in spans]
    roots = [s for s in spans if s.parent_id is None]
    if len(roots) != 1:
        warnings.add("root_span_count", f"trace has {len(roots)} root spans, not one")
    root = roots[0] if roots else None
    return ParsedTrace(
        source="arize",
        raw_trace_id=spans[0].trace_id,
        steps=steps,
        label=root.name if root else "",
        declared_complete=otel.declared_complete(spans, warnings),
        root_start_ns=root.start_ns if root else None,
        root_end_ns=root.end_ns if root else None,
    )


def parse(raw: Any, trace_id: str | None, warnings: Warnings) -> ParsedTrace:
    if isinstance(raw, dict) and "resourceSpans" in raw:
        return parse_otlp(raw, trace_id, warnings)
    return parse_phoenix(raw, trace_id, warnings)

