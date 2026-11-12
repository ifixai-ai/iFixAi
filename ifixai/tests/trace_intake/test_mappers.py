"""Every mapper against its fixtures: what each event kind is, and what is never inferred."""

from __future__ import annotations

import pytest

from ifixai.trace_intake import import_trace, validate_rows
from ifixai.trace_intake.builder import UNATTRIBUTED_ACTOR

SOURCES = ("otel", "langfuse", "langsmith", "arize", "braintrust")
AUTHORITY = {"approval", "revocation", "delegation"}


def kinds(result):
    return [row["payload"]["kind"] for row in result.events]


def run(fixtures, name, source="auto", **kwargs):
    return import_trace(fixtures / name, source, tenant_id="acme", collector_id="col-1", **kwargs)


def codes(result):
    return {warning.split("]")[0][1:] for warning in result.warnings}


@pytest.mark.parametrize("source", SOURCES)
def test_clean_trace_maps_tools_sources_and_outputs(fixtures, source):
    result = run(fixtures, f"{source}_clean.json")
    assert result.source == source
    validate_rows(result.capture_rows)
    found = kinds(result)
    assert found[0] == "start" and found[-1] == "end"
    assert "action" in found and "output" in found
    assert not AUTHORITY & set(found)
    assert result.header["tenant_id"] == "acme"
    assert result.header["collector_id"] == "col-1"
    assert result.header["capture_complete"] is False
    output = next(r for r in result.events if r["payload"]["kind"] == "output")
    assert output["payload"]["label"] == "Your order ORD-1001 has shipped."
    assert output["payload"]["citations"] == []
    times = [row["time_ns"] for row in result.events]
    assert times == sorted(times)


@pytest.mark.parametrize("source", SOURCES)
def test_failed_tool_is_failed(fixtures, source):
    result = run(fixtures, f"{source}_failed_tool.json")
    actions = [r["payload"] for r in result.events if r["payload"]["kind"] == "action"]
    assert len(actions) == 1
    assert actions[0]["outcome"] == "failed"
    assert actions[0]["action"] == "charge_card"
    assert actions[0]["resource"] == "tool:charge_card"


@pytest.mark.parametrize("source", SOURCES)
def test_approve_refund_names_never_become_approvals(fixtures, source):
    result = run(fixtures, f"{source}_approve_refund.json")
    assert not AUTHORITY & set(kinds(result))
    assert result.header["capture_complete"] is False
    assert result.warnings


def test_otel_clean_details(fixtures):
    result = run(fixtures, "otel_clean.json")
    payloads = [r["payload"] for r in result.events]
    source = payloads[1]
    assert source["kind"] == "source" and source["resource"] == "kb://orders/policy-7"
    tools = [p for p in payloads if p["kind"] == "action"]
    assert [t["action"] for t in tools] == ["lookup_order", "send_email"]
    assert all(t["outcome"] == "completed" for t in tools)
    assert {t["actor_id"] for t in tools} == {"agent-support-01"}
    assert result.events[0]["payload"]["label"] == "invoke_agent support-agent"
    assert {"citations_not_stated", "completeness_not_honoured"} <= codes(result)
    # Mapped events hang off the start because the agent span is structural.
    assert all(r["parents"] == ["ifixai-import-start"] for r in result.events[1:-1])


def test_otel_arguments_digest_matches_parsed_object(fixtures):
    from ifixai.trace_intake._core import canonical_digest

    result = run(fixtures, "otel_clean.json")
    lookup = next(r["payload"] for r in result.events if r["payload"].get("action") == "lookup_order")
    assert lookup["arguments_digest"] == canonical_digest({"order_id": "ORD-1001"})


def test_otel_two_traces_need_selection(fixtures):
    with pytest.raises(ValueError, match="--trace-id"):
        run(fixtures, "otel_two_traces.json")
    result = run(fixtures, "otel_two_traces.json", trace_id="0AF7651916CD43DD8448EB211C80319C")
    assert result.header["trace_id"] == "0af7651916cd43dd8448eb211c80319c"
    with pytest.raises(ValueError, match="absent"):
        run(fixtures, "otel_two_traces.json", trace_id="f" * 32)


def test_otel_malformed_refused(fixtures):
    with pytest.raises(ValueError, match="nonzero"):
        run(fixtures, "otel_malformed.json")


def test_langfuse_details(fixtures):
    result = run(fixtures, "langfuse_clean.json")
    assert result.header["trace_id"] == "7c1a2b3c4d5e4f608a1b2c3d4e5f6071"
    payloads = [r["payload"] for r in result.events]
    tools = [p for p in payloads if p["kind"] == "action"]
    assert [t["outcome"] for t in tools] == ["unknown", "unknown"]
    assert {t["actor_id"] for t in tools} == {"support-agent"}
    # The planning generation only requests a tool call: no output, no action from it.
    assert sum(p["kind"] == "output" for p in payloads) == 1
    assert "outcome_unknown" in codes(result)
    assert result.events[1]["parents"] == ["ifixai-import-start"]


def test_langfuse_approve_refund_warnings(fixtures):
    result = run(fixtures, "langfuse_approve_refund.json")
    assert {"untyped_span", "event_not_mapped", "actor_unattributed"} <= codes(result)
    output = next(r["payload"] for r in result.events if r["payload"]["kind"] == "output")
    assert output["actor_id"] == UNATTRIBUTED_ACTOR


def test_langsmith_details(fixtures):
    result = run(fixtures, "langsmith_clean.json")
    payloads = [r["payload"] for r in result.events]
    assert [p["kind"] for p in payloads] == ["start", "source", "action", "output", "end"]
    assert payloads[2]["actor_id"] == "agent-support-01"
    assert payloads[0]["label"] == "AgentExecutor"


def test_langsmith_nested_child_runs(fixtures):
    result = run(fixtures, "langsmith_failed_tool.json")
    assert kinds(result) == ["start", "action", "end"]


def test_max_text_caps_labels(fixtures):
    result = run(fixtures, "otel_clean.json", max_text=4)
    output = next(r["payload"] for r in result.events if r["payload"]["kind"] == "output")
    assert output["label"] == "Your"
    assert result.events[0]["payload"]["label"] == "invo"
    silent = run(fixtures, "otel_clean.json", max_text=0)
    assert all(r["payload"].get("label", "") == "" for r in silent.events)


def test_raw_bodies_are_not_copied(fixtures):
    text = run(fixtures, "langfuse_clean.json", max_text=0).to_jsonl()
    for secret_ish in ("customer@example.test", "ORD-1001", "Orders ship within"):
        assert secret_ish not in text
