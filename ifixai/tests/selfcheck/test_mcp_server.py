"""The local MCP server: protocol, tools, and a real stdio session."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ifixai.selfcheck import regressions
from ifixai.selfcheck.mcp_server import PROTOCOL_VERSIONS, RESULT_PATH, TOOLS, Server
from ifixai.tests.selfcheck.scorecards import scorecard, write


def project(tmp_path: Path) -> Path:
    report = write(tmp_path / ".ifixai" / "self-check" / "report.json", scorecard())
    pointer = {"report_path": report.relative_to(tmp_path).as_posix()}
    (tmp_path / RESULT_PATH).write_text(json.dumps(pointer), encoding="utf-8")
    return tmp_path


def call(server: Server, name: str, arguments: dict | None = None) -> dict:
    response = server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}}
    )
    assert response is not None and "result" in response, response
    return response["result"]


def test_initialize_negotiates_and_lists_tools(tmp_path) -> None:
    server = Server(tmp_path)
    init = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert "not certified" in init["result"]["instructions"]
    unknown = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "1999"}})
    assert unknown["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["audit_agent", "list_findings", "explain_failure", "add_regression_test"]
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"] == {}


def test_protocol_errors(tmp_path) -> None:
    server = Server(tmp_path)
    assert server.handle({"id": 1})["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})["error"]["code"] == -32601
    assert server.handle({"jsonrpc": "2.0", "method": "nope"}) is None
    bad = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "zzz"}}
    assert server.handle(bad)["error"]["code"] == -32602
    bad_args = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_findings", "arguments": []}}
    assert server.handle(bad_args)["error"]["code"] == -32602


def test_list_explain_and_pin(tmp_path) -> None:
    server = Server(project(tmp_path))
    listed = call(server, "list_findings")
    assert listed["isError"] is False
    assert listed["structuredContent"]["certified"] is False
    assert listed["structuredContent"]["findings"][0]["inspection"] == "B08"
    assert listed["content"][0]["text"].startswith("Self-check, not certified")

    explained = call(server, "explain_failure", {"inspection": "B08"})
    assert "What it did instead" in explained["content"][0]["text"]
    assert call(server, "explain_failure", {"inspection": "Z99"})["isError"] is True
    assert call(server, "explain_failure")["isError"] is True  # missing argument

    pinned = call(server, "add_regression_test", {"inspection": "B08", "note": "no admin grants"})
    assert pinned["structuredContent"]["added"] is True
    assert regressions.load(tmp_path / regressions.DEFAULT_PATH)[0]["note"] == "no admin grants"
    again = call(server, "add_regression_test", {"inspection": "B08"})
    assert again["structuredContent"]["added"] is False
    assert call(server, "add_regression_test", {"inspection": "B12"})["isError"] is True


def test_paths_stay_inside_the_project(tmp_path) -> None:
    server = Server(project(tmp_path / "p"))
    outside = write(tmp_path / "elsewhere.json", scorecard())
    result = call(server, "list_findings", {"report": "../elsewhere.json"})
    assert result["isError"] is True and "outside the project" in result["content"][0]["text"]
    assert outside.exists()


def test_no_self_check_yet(tmp_path) -> None:
    result = call(Server(tmp_path), "list_findings")
    assert result["isError"] is True and "Run audit_agent first" in result["content"][0]["text"]
    (tmp_path / RESULT_PATH).parent.mkdir(parents=True)
    (tmp_path / RESULT_PATH).write_text("{", encoding="utf-8")
    assert call(Server(tmp_path), "list_findings")["isError"] is True
    (tmp_path / RESULT_PATH).write_text("{}", encoding="utf-8")
    assert "names no report" in call(Server(tmp_path), "list_findings")["content"][0]["text"]


def fake_audit(stdout: str, code: int = 1, stderr: str = ""):
    seen: list[list[str]] = []

    def run(argv, root):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr=stderr)

    return run, seen


def test_audit_requires_confirmation_and_passes_flags(tmp_path) -> None:
    run, seen = fake_audit("progress\n" + json.dumps({"label": "self-check", "verdict": "Self-check, not certified: x"}))
    server = Server(tmp_path, run_audit=run)
    refused = call(server, "audit_agent", {"agent_url": "https://a.example/v1", "confirm_sandboxed": False})
    assert refused["isError"] is True and not seen
    done = call(server, "audit_agent", {"agent_url": "https://a.example/v1", "confirm_sandboxed": True,
                                        "suite": "strategic", "judge_provider": "mock"})
    assert done["isError"] is False
    assert done["structuredContent"]["exit_code"] == 1
    assert seen == [["audit", "https://a.example/v1", "--yes", "--json", "--suite", "strategic", "--judge-provider", "mock"]]


@pytest.mark.parametrize(("stdout", "stderr"), [("", "Error: boom\n"), ("{not json", "")])
def test_audit_failures_are_tool_errors(tmp_path, stdout, stderr) -> None:
    run, _ = fake_audit(stdout, code=2, stderr=stderr)
    result = call(Server(tmp_path, run_audit=run), "audit_agent", {"agent_url": "https://a.example", "confirm_sandboxed": True})
    assert result["isError"] is True


def test_audit_timeout_is_a_tool_error(tmp_path) -> None:
    def run(argv, root):
        raise subprocess.TimeoutExpired(argv, 1)

    result = call(Server(tmp_path, run_audit=run), "audit_agent", {"agent_url": "https://a.example", "confirm_sandboxed": True})
    assert result["isError"] is True and "stopped" in result["content"][0]["text"]


def test_serve_loop_survives_bad_lines(tmp_path) -> None:
    lines = "\n".join([
        "not json",
        "",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
    ]) + "\n"
    out = io.StringIO()
    Server(tmp_path).serve(io.StringIO(lines), out)
    replies = [json.loads(x) for x in out.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1] == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert len(replies) == 2


def test_serve_loop_turns_crashes_into_internal_errors(tmp_path, monkeypatch) -> None:
    server = Server(tmp_path)
    monkeypatch.setattr(server, "handle", lambda m: 1 / 0)
    out = io.StringIO()
    server.serve(io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n"), out)
    assert json.loads(out.getvalue())["error"] == {"code": -32603, "message": "Internal error"}


def test_tool_schemas_are_strict() -> None:
    for tool in TOOLS:
        assert tool["inputSchema"]["additionalProperties"] is False
        assert set(tool["inputSchema"].get("required", [])) <= set(tool["inputSchema"]["properties"])


def test_real_stdio_session(tmp_path) -> None:
    """`ifixai mcp` as a client launches it: a child process speaking JSON lines."""
    root = project(tmp_path)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_findings", "arguments": {}}},
    ]
    completed = subprocess.run(
        [sys.executable, "-m", "ifixai.cli.main", "mcp", "--root", str(root)],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    replies = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[0]["result"]["serverInfo"]["name"] == "ifixai-local"
    assert replies[1]["result"]["structuredContent"]["findings"][0]["inspection"] == "B08"
