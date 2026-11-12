"""Braintrust span export: rows with `span_attributes.type`, `span_parents` and `metrics`.

Mapping (by `span_attributes.type`):
  tool  -> action (span name, `input` as arguments)
  llm   -> output (final text of `output`)
  task, function, eval, score, automation -> structural
Braintrust records no success status, so a tool's outcome is `failed` when `error` is set and
`unknown` otherwise. Times come from `metrics.start` / `metrics.end` (Unix seconds).
"""

from __future__ import annotations

from typing import Any

from ifixai.trace_intake._core import (
    MAX_EVENTS,
    TraceIntakeError,
    Warnings,
    seconds_to_ns,
)
from ifixai.trace_intake.builder import MISSING, ParsedTrace, Step, select_trace
from ifixai.trace_intake.text import final_text

STRUCTURAL = {"task", "function", "eval", "score", "automation"}


def find_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        for key in ("events", "spans", "data"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = [raw]
    if not isinstance(raw, list) or not all(
        isinstance(row, dict) and "span_attributes" in row for row in raw
    ):
        raise TraceIntakeError("Braintrust export must be span rows with span_attributes")
    if len(raw) > MAX_EVENTS:
        raise TraceIntakeError(f"Braintrust export exceeds the {MAX_EVENTS} span limit")
    return raw


def moment(value: Any, warnings: Warnings, where: str) -> int | None:
    if value is None:
        return None
    parsed = seconds_to_ns(value)
    if parsed is None:
        warnings.add("timestamp_unparseable", "metrics time is not Unix seconds; ignored", where)
    return parsed


def row_step(row: dict[str, Any], warnings: Warnings) -> Step:
    span_id = str(row.get("span_id") or row.get("id") or "")
    if not span_id:
        raise TraceIntakeError("every Braintrust span needs a span_id")
    attrs = row["span_attributes"] if isinstance(row["span_attributes"], dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    parents = row.get("span_parents") or []
    if not isinstance(parents, list):
        raise TraceIntakeError("Braintrust span_parents must be a list")
    if len(parents) > 1:
        warnings.add("multiple_parents", "span lists several parents; the first is used", span_id)
    kind = str(attrs.get("type") or "").lower()
    name = attrs.get("name") if isinstance(attrs.get("name"), str) else None
    step = Step(
        step_id=span_id,
        parent_id=str(parents[0]) if parents else None,
        start_ns=moment(metrics.get("start"), warnings, span_id),
        end_ns=moment(metrics.get("end"), warnings, span_id),
        actor_id=metadata.get("ifixai.actor_id"),
        explicit=metadata.get("ifixai.event"),
    )
    if kind == "tool":
        step.kind = "action"
        step.action = name
        step.arguments = row.get("input", MISSING)
        step.outcome = "failed" if row.get("error") else "unknown"
    elif kind == "llm":
        step.kind = "output"
        step.text = final_text(row.get("output"))
    elif kind not in STRUCTURAL and step.explicit is None:
        warnings.add("unknown_span_kind", f"span type {kind[:40] or 'missing'!r} is not mapped",
                     span_id)
    return step


def parse(raw: Any, trace_id: str | None, warnings: Warnings) -> ParsedTrace:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in find_rows(raw):
        root_id = row.get("root_span_id")
        if not root_id:
            raise TraceIntakeError("every Braintrust span needs a root_span_id")
        groups.setdefault(str(root_id), []).append(row)
    selected = select_trace(groups, trace_id, "braintrust")
    rows = groups[selected]
    steps = [row_step(row, warnings) for row in rows]
    root = next((row for row in rows if str(row.get("span_id")) == selected), None)
    if root is None:
        warnings.add("root_span_missing", "the root span is absent from the export")
    root_step = next((step for step in steps if step.step_id == selected), None)
    root_attrs = root["span_attributes"] if root and isinstance(root["span_attributes"], dict) else {}
    root_meta = root.get("metadata") if root and isinstance(root.get("metadata"), dict) else {}
    return ParsedTrace(
        source="braintrust",
        raw_trace_id=selected,
        steps=steps,
        label=root_attrs.get("name") if isinstance(root_attrs.get("name"), str) else "",
        declared_complete=root_meta.get("ifixai.capture_complete") is True,
        root_start_ns=root_step.start_ns if root_step else None,
        root_end_ns=root_step.end_ns if root_step else None,
    )
