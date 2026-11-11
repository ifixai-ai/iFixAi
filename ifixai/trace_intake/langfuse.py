"""Langfuse trace export: a trace object with its full `observations`.

Mapping (by observation `type`):
  TOOL       -> action (name, input as arguments)
  GENERATION -> output (final text of `output`)
  RETRIEVER  -> source (documents in `output`)
  AGENT      -> structural; its name is the agent for its descendants
  SPAN, CHAIN, EMBEDDING, EVALUATOR, GUARDRAIL -> structural
  EVENT      -> not mapped (warned)
Langfuse records no success status, so a tool's outcome is `failed` only when its level is
ERROR and `unknown` otherwise. A plain SPAN is never read as a tool call, whatever its name.
"""

from __future__ import annotations

from typing import Any

from ifixai.trace_intake._core import MAX_EVENTS, TraceIntakeError, Warnings, iso_to_ns
from ifixai.trace_intake.builder import MISSING, ParsedTrace, Step, select_trace
from ifixai.trace_intake.text import documents_from, final_text

STRUCTURAL = {"SPAN", "CHAIN", "EMBEDDING", "EVALUATOR", "GUARDRAIL", "AGENT"}


def find_traces(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        raw = raw["data"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not all(
        isinstance(item, dict) and "observations" in item for item in raw
    ):
        raise TraceIntakeError("Langfuse export must be trace objects with observations")
    return raw


def timestamp(value: Any, warnings: Warnings, where: str) -> int | None:
    if value is None:
        return None
    parsed = iso_to_ns(value)
    if parsed is None:
        warnings.add("timestamp_unparseable", "timestamp is not ISO-8601; ignored", where)
    return parsed


def metadata_of(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def observation_step(obs: dict[str, Any], warnings: Warnings) -> Step:
    obs_id = obs.get("id")
    if not isinstance(obs_id, str) or not obs_id:
        raise TraceIntakeError("every Langfuse observation needs a string id")
    metadata = metadata_of(obs)
    kind = str(obs.get("type") or "").upper()
    name = obs.get("name") if isinstance(obs.get("name"), str) else None
    step = Step(
        step_id=obs_id,
        parent_id=obs.get("parentObservationId") or None,
        start_ns=timestamp(obs.get("startTime"), warnings, obs_id),
        end_ns=timestamp(obs.get("endTime"), warnings, obs_id),
        actor_id=metadata.get("ifixai.actor_id"),
        explicit=metadata.get("ifixai.event"),
    )
    if kind == "TOOL":
        step.kind = "action"
        step.action = name
        step.arguments = obs.get("input", MISSING)
        step.outcome = "failed" if obs.get("level") == "ERROR" else "unknown"
    elif kind == "GENERATION":
        step.kind = "output"
        step.text = final_text(obs.get("output"))
    elif kind == "RETRIEVER":
        step.kind = "source"
        step.documents = documents_from(obs.get("output"))
    elif kind == "EVENT":
        if step.explicit is None:
            warnings.add("event_not_mapped", "EVENT observations are not mapped", obs_id)
    elif kind in STRUCTURAL:
        if kind == "AGENT":
            step.agent = name
        if kind == "SPAN" and step.explicit is None:
            warnings.add(
                "untyped_span",
                "SPAN observations state no tool, generation or retrieval semantics; not mapped",
                obs_id,
            )
    else:
        warnings.add("unknown_span_kind", f"observation type {kind[:40]!r} is not mapped", obs_id)
    return step


def parse(raw: Any, trace_id: str | None, warnings: Warnings) -> ParsedTrace:
    traces = find_traces(raw)
    groups: dict[str, dict[str, Any]] = {}
    for trace in traces:
        raw_id = trace.get("id")
        if not isinstance(raw_id, str) or not raw_id:
            raise TraceIntakeError("every Langfuse trace needs a string id")
        if raw_id in groups:
            raise TraceIntakeError("duplicate Langfuse trace id")
        groups[raw_id] = trace
    selected = select_trace(groups, trace_id, "langfuse")
    trace = groups[selected]
    observations = trace["observations"]
    if not isinstance(observations, list):
        raise TraceIntakeError("Langfuse observations must be a list")
    if len(observations) > MAX_EVENTS:
        raise TraceIntakeError(f"Langfuse trace exceeds the {MAX_EVENTS} observation limit")
    steps = []
    for obs in observations:
        if not isinstance(obs, dict):
            warnings.add(
                "observation_body_missing",
                "observation is listed by id only; its body is absent from the export",
            )
            continue
        if obs.get("traceId") not in (None, selected):
            warnings.add("foreign_observation", "observation of another trace was skipped",
                         str(obs.get("id"))[:80])
            continue
        steps.append(observation_step(obs, warnings))
    metadata = metadata_of(trace)
    return ParsedTrace(
        source="langfuse",
        raw_trace_id=selected,
        steps=steps,
        label=trace.get("name") if isinstance(trace.get("name"), str) else "",
        declared_complete=metadata.get("ifixai.capture_complete") is True,
        root_start_ns=timestamp(trace.get("timestamp"), warnings, selected),
    )
