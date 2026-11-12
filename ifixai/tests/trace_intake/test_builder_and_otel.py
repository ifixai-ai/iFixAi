"""Builder rules and OTel edge cases driven through small in-memory exports."""

from __future__ import annotations

import json

import pytest

from ifixai.trace_intake import TraceIntakeError, import_document
from ifixai.trace_intake._core import Warnings, canonical_digest
from ifixai.trace_intake.builder import (
    START_ID,
    ParsedTrace,
    Step,
    build_rows,
    canonical_trace_id,
)

from .conftest import TRACE, otlp, otlp_pair, span

TOOL = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup",
        "gen_ai.tool.call.arguments": "{\"q\": 1}", "gen_ai.agent.id": "agent-1"}
DIGEST = "a" * 64


def imp(raw, source="otel", **kwargs):
    kwargs.setdefault("tenant_id", "acme")
    kwargs.setdefault("collector_id", "col")
    return import_document(raw, source, **kwargs)


def codes(result):
    return {w.split("]")[0][1:] for w in result.warnings}


def test_complete_only_when_declared_and_gap_free():
    spans = [span("a" * 16, 0, 10, TOOL, code=1)]
    assert imp(otlp(spans, complete=True)).header["capture_complete"] is True
    assert imp(otlp(spans, complete=True)).events[-1]["payload"]["complete"] is True
    assert imp(otlp(spans)).header["capture_complete"] is False
    gap = imp(otlp([*spans, span("b" * 16, 0, 5, {})], complete=True))
    assert gap.header["capture_complete"] is False
    assert "completeness_not_honoured" in codes(gap)


def test_capture_complete_must_be_boolean():
    raw = otlp([span("a" * 16, 0, 10, TOOL, code=1)])
    raw["resourceSpans"][0]["resource"]["attributes"].append(
        otlp_pair("ifixai.capture_complete", {"stringValue": "true"}))
    result = imp(raw)
    assert result.header["capture_complete"] is False
    assert "capture_complete_invalid" in codes(result)


def test_trace_tenant_and_collector_never_taken_from_trace():
    raw = otlp([span("a" * 16, 0, 10, TOOL, code=1)])
    raw["resourceSpans"][0]["resource"]["attributes"] += [
        otlp_pair("ifixai.tenant_id", {"stringValue": "evil"})]
    result = imp(raw)
    assert result.header["tenant_id"] == "acme"
    assert result.header["collector_id"] == "col"


def test_explicit_ifixai_event_is_the_only_way_to_an_approval():
    approval = {"kind": "approval", "actor_id": "manager-1", "subject_id": "agent-1",
                "action": "refund", "resource": "tool:refund", "arguments_digest": DIGEST,
                "expires_ns": 2**62}
    action_event = {"event_id": "c" * 16, "trace_id": TRACE, "parents": ["b" * 16],
                    "payload": {"kind": "action", "actor_id": "agent-1", "action": "refund",
                                "resource": "tool:refund", "arguments_digest": DIGEST,
                                "approval_id": "b" * 16, "outcome": "completed"}}
    spans = [
        span("a" * 16, 0, 100, {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.id": "agent-1"}),
        span("b" * 16, 10, 20, {"ifixai.event": json.dumps(approval)}, "a" * 16),
        span("c" * 16, 30, 40, {"ifixai.event": json.dumps(action_event)}, "a" * 16, code=1),
    ]
    result = imp(otlp(spans))
    rows = result.events
    assert [r["payload"]["kind"] for r in rows] == ["start", "approval", "action", "end"]
    assert rows[2]["parents"] == ["b" * 16]
    assert rows[2]["payload"]["approval_id"] == "b" * 16


def test_explicit_event_binding_is_enforced():
    bad = {"event_id": "z" * 16, "payload": {"kind": "output", "actor_id": "a"}}
    with pytest.raises(TraceIntakeError, match="not bound"):
        imp(otlp([span("a" * 16, 0, 10, {"ifixai.event": json.dumps(bad)})]))
    wrong_tenant = {"tenant_id": "other", "payload": {"kind": "output", "actor_id": "a"}}
    with pytest.raises(TraceIntakeError, match="not bound"):
        imp(otlp([span("a" * 16, 0, 10, {"ifixai.event": json.dumps(wrong_tenant)})]))
    with pytest.raises(TraceIntakeError, match="valid JSON"):
        imp(otlp([span("a" * 16, 0, 10, {"ifixai.event": "{nope"})]))
    with pytest.raises(TraceIntakeError, match="JSON object"):
        imp(otlp([span("a" * 16, 0, 10, {"ifixai.event": "[1]"})]))
    with pytest.raises(TraceIntakeError, match="fields"):
        imp(otlp([span("a" * 16, 0, 10, {"ifixai.event": json.dumps(
            {"payload": {"kind": "output", "actor_id": "a"}, "extra": 1})})]))
    with pytest.raises(TraceIntakeError, match="invalid canonical payload"):
        imp(otlp([span("a" * 16, 0, 10, {"ifixai.event": json.dumps({"kind": "approval"})})]))
    with pytest.raises(TraceIntakeError, match="parents"):
        imp(otlp([span("a" * 16, 0, 10, {"ifixai.event": json.dumps(
            {"parents": "x", "payload": {"kind": "output", "actor_id": "a"}})})]))


def test_explicit_boundaries_and_bad_parents_are_warned():
    spans = [
        span("a" * 16, 0, 10, {"ifixai.event": json.dumps({"kind": "start"})}),
        span("b" * 16, 20, 30, {"ifixai.event": json.dumps(
            {"parents": ["missing"], "payload": {"kind": "output", "actor_id": "a", "label": "x"}})}),
    ]
    result = imp(otlp(spans))
    assert {"explicit_boundary_ignored", "explicit_parents_invalid"} <= codes(result)
    assert result.events[1]["parents"] == [START_ID]


def test_otel_outcomes_and_semantics_warnings():
    spans = [
        span("a" * 16, 0, 10, {**TOOL}),
        span("b" * 16, 10, 20, {**TOOL, "error.type": "Timeout"}),
        span("c" * 16, 20, 30, {"gen_ai.operation.name": "teleport"}),
        span("d" * 16, 30, 40, {"gen_ai.operation.name": "embeddings"}),
        span("e" * 16, 40, 50, {"gen_ai.operation.name": "execute_tool", "ifixai.weird": "1"}),
        span("f" * 16, 50, 60, {"gen_ai.operation.name": "retrieval"}),
        span("1" * 16, 60, 70, {"gen_ai.operation.name": "chat",
                                "gen_ai.completion.0.content": "done [truncated]"}),
    ]
    spans[0]["status"] = {"code": "STATUS_CODE_UNSET"}
    result = imp(otlp(spans))
    payloads = [r["payload"] for r in result.events]
    actions = [p for p in payloads if p["kind"] == "action"]
    assert [a["outcome"] for a in actions] == ["unknown", "failed", "unknown"]
    assert actions[2]["action"] == "unknown-tool"
    assert actions[2]["arguments_digest"] == canonical_digest(None)
    found = codes(result)
    assert {"unknown_span_kind", "unknown_reserved_attribute", "tool_name_missing",
            "tool_arguments_missing", "outcome_unknown", "retrieval_documents_missing",
            "content_truncated", "root_span_count"} <= found
    source = next(p for p in payloads if p["kind"] == "source")
    assert source["resource"] == "retrieval:" + "f" * 16


def test_dropped_counts_and_missing_times_warn():
    first = span("a" * 16, 0, 10, TOOL, code=1)
    first["droppedAttributesCount"] = 2
    no_end = span("b" * 16, 5, 6, TOOL, "a" * 16, code=1)
    del no_end["endTimeUnixNano"]
    no_time = span("c" * 16, 0, 0, TOOL, "a" * 16, code=1)
    del no_time["endTimeUnixNano"], no_time["startTimeUnixNano"]
    raw = otlp([first, no_end, no_time])
    raw["resourceSpans"][0]["resource"]["droppedAttributesCount"] = 1
    result = imp(raw)
    assert {"dropped_attributes", "end_time_missing", "timestamp_missing"} <= codes(result)


def test_orphans_cycles_and_duplicates():
    orphan = imp(otlp([span("a" * 16, 0, 10, TOOL, "9" * 16, code=1)]))
    assert {"orphan_parent", "root_span_count"} <= codes(orphan)
    with pytest.raises(TraceIntakeError, match="cycle"):
        imp(otlp([span("a" * 16, 0, 10, TOOL, "b" * 16), span("b" * 16, 0, 10, TOOL, "a" * 16)]))
    with pytest.raises(TraceIntakeError, match="duplicate span"):
        imp(otlp([span("a" * 16, 0, 10, TOOL), span("a" * 16, 0, 10, TOOL)]))
    with pytest.raises(TraceIntakeError, match="before it starts"):
        imp(otlp([span("a" * 16, 10, 5, TOOL)]))


def test_nested_mapped_parent_is_used_when_it_precedes():
    spans = [
        span("a" * 16, 0, 5, TOOL, code=1),
        span("b" * 16, 1, 10, TOOL, "a" * 16, code=1),
        span("c" * 16, 2, 3, TOOL, "a" * 16, code=1),
    ]
    rows = imp(otlp(spans)).events
    by_id = {r["event_id"]: r for r in rows}
    assert by_id["b" * 16]["parents"] == ["a" * 16]
    # c ends before its parent a, so it cannot depend on it.
    assert by_id["c" * 16]["parents"] == [START_ID]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda s: s.update(startTimeUnixNano=1.5), "exact"),
        (lambda s: s.update(status={"code": 9}), "status"),
        (lambda s: s.update(attributes=[otlp_pair(1, {"stringValue": "x"})]), "keys"),
        (lambda s: s.update(attributes=[otlp_pair("k", {"stringValue": "x"})] * 2), "duplicate"),
        (lambda s: s.update(attributes=[otlp_pair("k", {"mystery": 1})]), "AnyValue"),
        (lambda s: s.update(attributes=[otlp_pair("k", {})]), "AnyValue"),
        (lambda s: s.update(attributes={}), "array"),
        (lambda s: s.update(startTimeUnixNano=str(2**64)), "range"),
    ],
)
def test_malformed_otlp_refused(mutate, message):
    item = span("a" * 16, 0, 10, TOOL)
    mutate(item)
    with pytest.raises(TraceIntakeError, match=message):
        imp(otlp([item]))


def test_any_value_variants():
    item = span("a" * 16, 0, 10, TOOL, code=1)
    item["attributes"] += [
        otlp_pair("list", {"arrayValue": {"values": [{"intValue": "3"}, {"boolValue": True}]}}),
        otlp_pair("map", {"kvlistValue": {"values": [otlp_pair("x", {"doubleValue": 1.5})]}}),
    ]
    assert imp(otlp([item])).events[1]["payload"]["kind"] == "action"
    with pytest.raises(TraceIntakeError):
        imp([1, 2], "otel")


def test_identifiers_and_resources_are_sanitized():
    weird = {**TOOL, "gen_ai.tool.name": "x" * 300, "gen_ai.agent.id": " padded "}
    star = {**TOOL, "gen_ai.tool.name": "files.*"}
    result = imp(otlp([span("a" * 16, 0, 10, weird, code=1), span("b" * 16, 10, 20, star, code=1)]))
    payloads = [r["payload"] for r in result.events if r["payload"]["kind"] == "action"]
    assert payloads[0]["action"].startswith("id-")
    assert payloads[0]["actor_id"].startswith("id-")
    assert payloads[1]["resource"] == "tool:files.%2A"
    assert {"tool_name_rewritten", "actor_rewritten", "resource_wildcard_escaped"} <= codes(result)


def test_canonical_trace_id_forms():
    warnings = Warnings()
    assert canonical_trace_id("x", "AB" * 16) == "ab" * 16
    assert canonical_trace_id("x", "7c1a2b3c-4d5e-4f60-8a1b-2c3d4e5f6071") == "7c1a2b3c4d5e4f608a1b2c3d4e5f6071"
    derived = canonical_trace_id("x", "trace-123", warnings)
    assert len(derived) == 32 and "trace_id_derived" in warnings


def test_builder_direct_edge_cases():
    warnings = Warnings()
    parsed = ParsedTrace("otel", TRACE, [Step("bad id ", kind="output", text="hi")])
    rows = build_rows(parsed, tenant_id="t", collector_id="c", max_text=10, warnings=warnings)
    assert rows[2]["event_id"].startswith("id-")
    assert {"span_id_rewritten", "no_timestamps", "actor_unattributed"} <= set(warnings.entries)
    with pytest.raises(TraceIntakeError, match="reserved"):
        build_rows(ParsedTrace("otel", TRACE, [Step(START_ID)]), tenant_id="t",
                   collector_id="c", max_text=1, warnings=Warnings())
    with pytest.raises(TraceIntakeError, match="unsupported"):
        build_rows(ParsedTrace("otel", TRACE, [Step("s", kind="weird", end_ns=1)]),
                   tenant_id="t", collector_id="c", max_text=1, warnings=Warnings())
    empty = build_rows(ParsedTrace("otel", TRACE, []), tenant_id="t", collector_id="c",
                       max_text=1, warnings=(w := Warnings()))
    assert len(empty) == 3 and "no_facts" in w


def test_event_cap(monkeypatch):
    from ifixai.trace_intake import builder

    monkeypatch.setattr(builder, "MAX_EVENTS", 3)
    spans = [span(f"{i:016x}", i, i + 1, TOOL, code=1) for i in range(1, 4)]
    with pytest.raises(TraceIntakeError, match="event limit"):
        imp(otlp(spans))


def test_document_ids_and_multiple_documents():
    docs = json.dumps([{"id": "doc-1", "content": "a"}, {"content": "b [redacted]"}])
    result = imp(otlp([span("a" * 16, 0, 10, {"gen_ai.operation.name": "retrieval",
                                             "gen_ai.data_source.id": "kb",
                                             "gen_ai.retrieval.documents": docs})]))
    sources = [r for r in result.events if r["payload"]["kind"] == "source"]
    assert [s["event_id"] for s in sources] == ["a" * 16 + "#doc0", "a" * 16 + "#doc1"]
    assert [s["payload"]["resource"] for s in sources] == ["doc-1", "retrieval:kb#1"]
    assert {"document_id_missing", "content_truncated"} <= codes(result)
