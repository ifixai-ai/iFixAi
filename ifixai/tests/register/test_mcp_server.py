"""The MCP server: it speaks the protocol, and it prepares without sending."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from ifixai.register import mcp, store
from ifixai.register.spec import MisbehaviourReport

pytestmark = pytest.mark.unit


def _call(name: str, arguments: dict) -> dict:
    response = mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )
    assert response is not None
    return response


def _text(response: dict) -> str:
    return response["result"]["content"][0]["text"]


def test_initialize_and_list_tools() -> None:
    initialized = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialized is not None
    assert initialized["result"]["serverInfo"]["name"] == mcp.SERVER_NAME
    listed = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None
    tools = listed["result"]["tools"]
    assert {tool["name"] for tool in tools} == {tool.name for tool in mcp.TOOLS}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"


def test_a_notification_gets_no_response() -> None:
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_an_unknown_method_is_an_error_not_a_crash() -> None:
    response = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/invoke"})
    assert response is not None and response["error"]["code"] == -32601


def test_an_unknown_tool_is_an_error() -> None:
    response = _call("register_submit", {})
    assert "error" in response


def test_describe_spec_names_every_required_field() -> None:
    text = _text(_call("register_describe_spec", {}))
    for phrase in (
        "world state",
        "identity and authorization",
        "never claimable in conversation",
        "verbatim",
        "FINAL WORLD STATE",
        "twin",
        "refuses everything scores perfectly",
        "never an LLM judge",
        "scores 1",
        "determinism",
        "occurrences out of",
        "rights-cleared",
        "runtime-neutral",
        "reference implementation, not the definition",
    ):
        assert phrase in text, f"the spec description does not mention {phrase!r}"


def test_compute_signature_reports_a_known_behaviour(entry: MisbehaviourReport, tmp_path: Path) -> None:
    root = tmp_path
    (root / "register" / "entries").mkdir(parents=True)
    (root / "register" / "entries" / "IFX-2026-0101.yaml").write_text(store.dump_yaml(entry), encoding="utf-8")
    text = _text(
        _call(
            "register_compute_signature",
            {
                "family": entry.signature.family,
                "failure": entry.signature.failure,
                "trigger": entry.signature.trigger,
                "register_root": str(root),
            },
        )
    )
    assert entry.signature.digest in text
    assert "already filed as" in text and "IFX-2026-0101" in text


def test_compute_signature_reports_an_unknown_behaviour(tmp_path: Path) -> None:
    text = _text(
        _call(
            "register_compute_signature",
            {"family": "novel_family", "failure": "novel.failure", "trigger": "novel_trigger",
             "register_root": str(tmp_path)},
        )
    )
    assert "No report in the register has this signature" in text


def test_validate_report_reports_problems(entry: MisbehaviourReport) -> None:
    payload = entry.model_dump(mode="json")
    payload["twin"]["request_text"] = "something else"
    text = _text(_call("register_validate_report", {"report": payload}))
    assert "does not pass" in text and "twin.request_text" in text


def test_validate_report_accepts_a_good_one(entry: MisbehaviourReport) -> None:
    text = _text(_call("register_validate_report", {"report": entry.model_dump(mode="json")}))
    assert "conforms to the spec" in text


def test_prepare_submission_writes_a_draft_and_sends_nothing(
    entry: MisbehaviourReport, tmp_path: Path
) -> None:
    out = tmp_path / "draft.yaml"
    records = list(entry.world.records)
    records[0] = records[0].model_copy(
        update={"fields": {**records[0].fields, "note": "Call Jane Okonkwo on +44 20 7946 0958."}}
    )
    leaky = entry.model_copy(update={"world": entry.world.model_copy(update={"records": tuple(records)})})
    text = _text(
        _call(
            "register_prepare_submission",
            {"report": leaky.model_dump(mode="json"), "out": str(out), "register_root": str(tmp_path)},
        )
    )
    assert out.exists()
    written = out.read_text(encoding="utf-8")
    assert "Jane Okonkwo" not in written
    assert "+44 20 7946 0958" not in written
    assert "PREPARED, NOT SENT" in text
    assert "ifixai report submit" in text
    assert str(out) in text
    # The draft on disk is a valid report, so the human's review is of something real.
    assert store.load_report(out)


def test_prepare_submission_does_not_show_the_agent_the_originals(
    entry: MisbehaviourReport, tmp_path: Path
) -> None:
    """The agent is told what kinds of value were replaced, never the values. The person at the
    keyboard reviews those in the side-by-side."""
    records = list(entry.world.records)
    records[0] = records[0].model_copy(
        update={"fields": {**records[0].fields, "note": "Jane Okonkwo, jane@acme.test, ACCT-99812736."}}
    )
    leaky = entry.model_copy(update={"world": entry.world.model_copy(update={"records": tuple(records)})})
    text = _text(
        _call(
            "register_prepare_submission",
            {
                "report": leaky.model_dump(mode="json"),
                "out": str(tmp_path / "draft.yaml"),
                "register_root": str(tmp_path),
            },
        )
    )
    for secret in ("Jane Okonkwo", "jane@acme.test", "99812736"):
        assert secret not in text


def test_prepare_submission_accepts_yaml_text(entry: MisbehaviourReport, tmp_path: Path) -> None:
    text = _text(
        _call(
            "register_prepare_submission",
            {
                "report": store.dump_yaml(entry),
                "out": str(tmp_path / "draft.yaml"),
                "register_root": str(tmp_path),
            },
        )
    )
    assert "Prepared a candidate" in text


def test_prepare_submission_refuses_a_malformed_report(tmp_path: Path) -> None:
    response = _call("register_prepare_submission", {"report": {"title": "incomplete"}})
    assert response["result"].get("isError") is True
    assert "does not match the spec" in _text(response)


def test_the_stdio_loop_round_trips() -> None:
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + "\n"
    )
    stdout = io.StringIO()
    mcp.serve(stdin, stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line]
    assert [response["id"] for response in responses] == [1, 2]


def test_a_malformed_line_is_a_parse_error_not_a_crash() -> None:
    stdout = io.StringIO()
    mcp.serve(io.StringIO("not json\n"), stdout)
    assert json.loads(stdout.getvalue())["error"]["code"] == -32700
