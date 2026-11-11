"""Langfuse, LangSmith, Arize and Braintrust edge cases."""

from __future__ import annotations

import json

import pytest

from ifixai.trace_intake import TraceIntakeError, import_document

from .conftest import TRACE, attr, otlp, span


def imp(raw, source, **kwargs):
    return import_document(raw, source, tenant_id="acme", collector_id="col", **kwargs)


def codes(result):
    return {w.split("]")[0][1:] for w in result.warnings}


def kinds(result):
    return [r["payload"]["kind"] for r in result.events]


# ---------- Langfuse ----------

def lf_obs(obs_id, kind, **kw):
    item = {"id": obs_id, "type": kind, "name": kind.lower(), "startTime": "2026-01-01T00:00:01Z",
            "endTime": "2026-01-01T00:00:02Z"}
    item.update(kw)
    return item


def test_langfuse_list_selection_and_complete():
    traces = {"data": [
        {"id": "trace-one", "metadata": {"ifixai.capture_complete": True}, "observations": [
            lf_obs("o1", "GENERATION", output={"choices": [{"message": {"content": "hi"}}]},
                   metadata={"ifixai.actor_id": "bot"})]},
        {"id": "trace-two", "observations": []},
    ]}
    with pytest.raises(TraceIntakeError, match="2 traces"):
        imp(traces, "langfuse")
    result = imp(traces, "langfuse", trace_id="trace-one")
    assert kinds(result) == ["start", "output", "end"]
    assert "trace_id_derived" in codes(result)
    assert result.header["capture_complete"] is False


def test_langfuse_edge_cases():
    raw = {"id": "t", "timestamp": "bad", "observations": [
        "obs-only-id",
        lf_obs("o1", "TOOL", traceId="other"),
        lf_obs("o2", "MYSTERY"),
        lf_obs("o3", "RETRIEVER", output=[{"text": "doc", "id": "d1"}, "plain"]),
        lf_obs("o4", "EVENT", metadata={"ifixai.event": {"kind": "output", "actor_id": "a"}}),
    ]}
    result = imp(raw, "langfuse")
    assert {"observation_body_missing", "foreign_observation", "unknown_span_kind",
            "timestamp_unparseable"} <= codes(result)
    assert kinds(result).count("source") == 2
    assert "output" in kinds(result)
    for bad in ({"id": "t"}, [{"observations": []}], {"id": "t", "observations": {}},
                {"id": "t", "observations": [{"type": "TOOL"}]}):
        with pytest.raises(TraceIntakeError):
            imp(bad, "langfuse")
    with pytest.raises(TraceIntakeError, match="duplicate"):
        imp([{"id": "t", "observations": []}, {"id": "t", "observations": []}], "langfuse")


# ---------- LangSmith ----------

def ls_run(run_id, run_type, **kw):
    item = {"id": run_id, "run_type": run_type, "name": run_type, "trace_id": "tr",
            "start_time": "2026-01-01T00:00:01", "end_time": "2026-01-01T00:00:02",
            "status": "success"}
    item.update(kw)
    return item


def test_langsmith_edge_cases():
    runs = [
        ls_run("r1", "chain", trace_id=None, extra={"metadata": {"ifixai.capture_complete": True}}),
        ls_run("r2", "tool", trace_id=None, parent_run_id="r1", status="pending", end_time=None),
        ls_run("r3", "mystery", trace_id=None, parent_run_id="r1"),
        ls_run("r4", "llm", trace_id=None, parent_run_id="r1", outputs={"output": "answer"}),
    ]
    result = imp(runs, "langsmith")
    assert {"run_unfinished", "unknown_span_kind"} <= codes(result)
    action = next(r["payload"] for r in result.events if r["payload"]["kind"] == "action")
    assert action["outcome"] == "unknown"
    assert result.header["capture_complete"] is False
    for bad in ("x", [{"id": "r"}], [ls_run("r", "tool", child_runs={})], [ls_run("", "tool")]):
        with pytest.raises(TraceIntakeError):
            imp(bad, "langsmith")


def test_langsmith_tool_error_field_and_single_run():
    result = imp(ls_run("r1", "tool", error="boom", status="success"), "langsmith")
    assert result.events[1]["payload"]["outcome"] == "failed"
    two = [ls_run("a", "chain"), ls_run("b", "chain", trace_id="other")]
    assert imp(two, "langsmith", trace_id="other").events[0]["payload"]["label"] == "chain"


def test_langsmith_bad_timestamp_and_cycle():
    result = imp([ls_run("r1", "tool", start_time="soon")], "langsmith")
    assert "timestamp_unparseable" in codes(result)
    cyc = [ls_run("a", "tool", trace_id=None, parent_run_id="b"),
           ls_run("b", "tool", trace_id=None, parent_run_id="a")]
    with pytest.raises(TraceIntakeError, match="cycle"):
        imp(cyc, "langsmith")


# ---------- Arize ----------

def px(span_id, kind, parent=None, status="OK", **attrs):
    return {"name": kind.lower(), "context": {"trace_id": TRACE, "span_id": span_id},
            "parent_id": parent, "span_kind": kind, "start_time": "2026-01-01T00:00:01Z",
            "end_time": "2026-01-01T00:00:02Z", "status_code": status, "attributes": attrs}


def test_arize_flat_attributes_and_kinds():
    spans = [
        px("r", "AGENT", **{"agent.name": "helper"}),
        px("t", "TOOL", "r", status="UNSET", **{"tool.name": "search",
           "tool_call.function.arguments": "{\"q\": \"x\"}"}),
        px("l", "LLM", "r", **{"output.value": json.dumps({"content": "fallback text"})}),
        px("u", "MYSTERY", "r"),
        {**px("m", "", "r"), "start_time": "never"},
    ]
    result = imp({"data": spans}, "arize")
    payloads = [r["payload"] for r in result.events]
    action = next(p for p in payloads if p["kind"] == "action")
    assert action["outcome"] == "unknown" and action["actor_id"] == "helper"
    output = next(p for p in payloads if p["kind"] == "output")
    assert output["label"] == "fallback text"
    assert {"unknown_span_kind", "timestamp_unparseable"} <= codes(result)
    for bad in ([1], [{"context": {}}], {"spans": "x"}):
        with pytest.raises(TraceIntakeError):
            imp(bad, "arize")


def test_arize_over_otlp_with_openinference():
    tool = {"openinference.span.kind": "TOOL", "tool.name": "lookup", "input.value": "{}"}
    root_attrs = {"openinference.span.kind": "CHAIN"}
    result = imp(otlp([span("a" * 16, 0, 10, root_attrs, name="root"),
                       span("b" * 16, 1, 5, tool, "a" * 16, code=2)], complete=True), "arize")
    assert kinds(result) == ["start", "action", "end"]
    assert result.events[1]["payload"]["outcome"] == "failed"
    assert result.events[0]["payload"]["label"] == "root"


def test_arize_complete_flag_on_root():
    spans = [px("r", "TOOL", **{"ifixai.capture_complete": True, "tool.name": "x",
                                "input.value": "{}", "agent.name": "bot"})]
    assert imp(spans, "arize").header["capture_complete"] is True


# ---------- Braintrust ----------

def bt(span_id, kind, parents=None, **kw):
    row = {"span_id": span_id, "root_span_id": "root", "span_parents": parents,
           "span_attributes": {"name": kind, "type": kind}, "metrics": {"start": 1, "end": 2}}
    row.update(kw)
    return row


def test_braintrust_edge_cases():
    rows = [
        bt("child", "tool", ["root", "other"], input={"a": 1}, metadata={"ifixai.actor_id": "bot"}),
        bt("x", "mystery", ["root"], metrics={"start": "soon"}),
    ]
    result = imp({"spans": rows}, "braintrust")
    assert {"multiple_parents", "root_span_missing", "orphan_parent", "unknown_span_kind",
            "timestamp_unparseable"} <= codes(result)
    for bad in ([{"x": 1}], [bt("", "tool")], [bt("a", "tool", root_span_id=None)],
                [bt("a", "tool", "root")]):
        with pytest.raises(TraceIntakeError):
            imp(bad, "braintrust")


def test_braintrust_complete_root():
    root = "b7a1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d"
    rows = [bt(root, "llm", root_span_id=root, output="all done", metadata={"ifixai.capture_complete": True,
                                                           "ifixai.actor_id": "bot"})]
    result = imp(rows[0], "braintrust")
    assert kinds(result) == ["start", "output", "end"]
    assert result.header["capture_complete"] is True


def test_otlp_attr_helper_ints():
    assert attr("n", 3)["value"] == {"intValue": "3"}
