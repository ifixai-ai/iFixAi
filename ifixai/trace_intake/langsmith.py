"""LangSmith run export: runs (flat, or nested through `child_runs`) of one or more traces.

Mapping (by `run_type`):
  tool      -> action (name, `inputs` as arguments)
  llm       -> output (final text of `outputs`)
  retriever -> source (`outputs.documents`)
  chain, prompt, parser, embedding -> structural
Outcome: `error` set or status "error" -> failed; status "success" -> completed; else unknown.
"""

from __future__ import annotations

from typing import Any

from ifixai.trace_intake._core import MAX_EVENTS, TraceIntakeError, Warnings, iso_to_ns
from ifixai.trace_intake.builder import MISSING, ParsedTrace, Step, select_trace
from ifixai.trace_intake.text import documents_from, final_text

STRUCTURAL = {"chain", "prompt", "parser", "embedding"}


def flatten_runs(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("runs"), list):
        raw = raw["runs"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise TraceIntakeError("LangSmith export must be a list of runs")
    runs: list[dict[str, Any]] = []
    stack = list(reversed(raw))
    while stack:
        run = stack.pop()
        if not isinstance(run, dict) or "run_type" not in run:
            raise TraceIntakeError("every LangSmith run needs a run_type")
        children = run.get("child_runs")
        children = [] if children is None else children
        if not isinstance(children, list):
            raise TraceIntakeError("LangSmith child_runs must be a list")
        for child in reversed(children):
            if isinstance(child, dict) and not child.get("parent_run_id"):
                child = {**child, "parent_run_id": run.get("id")}
            stack.append(child)
        runs.append(run)
        if len(runs) > MAX_EVENTS:
            raise TraceIntakeError(f"LangSmith export exceeds the {MAX_EVENTS} run limit")
    return runs


def metadata_of(run: dict[str, Any]) -> dict[str, Any]:
    extra = run.get("extra") if isinstance(run.get("extra"), dict) else {}
    metadata = extra.get("metadata", run.get("metadata"))
    return metadata if isinstance(metadata, dict) else {}


def timestamp(value: Any, warnings: Warnings, where: str) -> int | None:
    if value is None:
        return None
    parsed = iso_to_ns(value)
    if parsed is None:
        warnings.add("timestamp_unparseable", "timestamp is not ISO-8601; ignored", where)
    return parsed


def run_outcome(run: dict[str, Any], warnings: Warnings, where: str) -> str:
    status = run.get("status")
    if run.get("error") or status == "error":
        return "failed"
    if status == "success":
        return "completed"
    if status == "pending" or run.get("end_time") is None:
        warnings.add("run_unfinished", "run is pending or has no end time", where)
    return "unknown"


def run_step(run: dict[str, Any], warnings: Warnings) -> Step:
    run_id = str(run.get("id") or "")
    if not run_id:
        raise TraceIntakeError("every LangSmith run needs an id")
    metadata = metadata_of(run)
    run_type = str(run.get("run_type") or "").lower()
    parent = run.get("parent_run_id")
    step = Step(
        step_id=run_id,
        parent_id=str(parent) if parent else None,
        start_ns=timestamp(run.get("start_time"), warnings, run_id),
        end_ns=timestamp(run.get("end_time"), warnings, run_id),
        actor_id=metadata.get("ifixai.actor_id"),
        explicit=metadata.get("ifixai.event"),
    )
    outputs = run.get("outputs")
    if run_type == "tool":
        step.kind = "action"
        step.action = run.get("name") if isinstance(run.get("name"), str) else None
        step.arguments = run.get("inputs", MISSING)
        step.outcome = run_outcome(run, warnings, run_id)
    elif run_type == "llm":
        step.kind = "output"
        step.text = final_text(outputs)
    elif run_type == "retriever":
        step.kind = "source"
        step.documents = documents_from(outputs)
    elif run_type not in STRUCTURAL:
        warnings.add("unknown_span_kind", f"run_type {run_type[:40]!r} is not mapped", run_id)
    return step


def parse(raw: Any, trace_id: str | None, warnings: Warnings) -> ParsedTrace:
    runs = flatten_runs(raw)
    by_id = {str(run.get("id")): run for run in runs}
    groups: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        trace = run.get("trace_id")
        if not trace:
            current, seen = run, set()
            while current.get("parent_run_id") and str(current["parent_run_id"]) in by_id:
                if id(current) in seen:
                    raise TraceIntakeError("LangSmith parents form a cycle")
                seen.add(id(current))
                current = by_id[str(current["parent_run_id"])]
            trace = current.get("id")
        groups.setdefault(str(trace), []).append(run)
    selected = select_trace(groups, trace_id, "langsmith")
    selected_runs = groups[selected]
    roots = [run for run in selected_runs if not run.get("parent_run_id")]
    if len(roots) != 1:
        warnings.add("root_span_count", f"trace has {len(roots)} root runs, not one")
    root = roots[0] if roots else {}
    return ParsedTrace(
        source="langsmith",
        raw_trace_id=selected,
        steps=[run_step(run, warnings) for run in selected_runs],
        label=root.get("name") if isinstance(root.get("name"), str) else "",
        declared_complete=metadata_of(root).get("ifixai.capture_complete") is True,
        root_start_ns=timestamp(root.get("start_time"), warnings, selected),
        root_end_ns=timestamp(root.get("end_time"), warnings, selected),
    )
