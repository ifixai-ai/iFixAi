"""`ifixai mcp`: a local MCP server over stdio for Claude Code, Cursor and any MCP client.

Four tools, all working in one project folder (the server's root):

- `audit_agent`: self-check an agent URL (runs `ifixai audit` in a subprocess).
- `list_findings`: what the latest (or a given) report found.
- `explain_failure`: explain one failure in plain language, from the evidence.
- `add_regression_test`: pin a failure in `.ifixai/regressions.yaml`.

The protocol is JSON-RPC 2.0, one message per line on stdin and stdout, as the
MCP stdio transport specifies. It is implemented here rather than through an
SDK so the open core keeps no extra dependency; the surface is the handful of
methods a tools-only server needs (initialize, ping, tools/list, tools/call).
Stdout carries only protocol messages: the audit runs in a child process whose
output is captured, and every log line goes to stderr.

Everything this server returns is a self-check: never certified, no badge.
"""

from __future__ import annotations

import json

# Runs this same interpreter's ifixai CLI, never a shell.
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from ifixai._version import VERSION
from ifixai.selfcheck import regressions as regressions_mod
from ifixai.selfcheck.explain import explain
from ifixai.selfcheck.result import NOTICE, SelfCheckError, build_result, load_scorecard

SERVER_NAME = "ifixai-local"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
RESULT_PATH = Path(".ifixai") / "self-check" / "self-check.json"
AUDIT_TIMEOUT_SECONDS = 30 * 60
INSTRUCTIONS = (
    "iFixAi self-check tools. Results are self-checks: free local runs that are not "
    "certified and carry no badge; say so when you report them. audit_agent sends "
    "test prompts to the agent URL, so only use it on a test deployment the user "
    "has confirmed is sandboxed. To fix a failure: explain_failure, fix the agent, "
    "then add_regression_test so later audits keep checking it."
)

INSPECTION_PROP = {"type": "string", "description": "Inspection id from list_findings, e.g. B08."}
REPORT_PROP = {
    "type": "string",
    "description": "Report JSON path inside the project. Default: the latest self-check's report.",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "audit_agent",
        "title": "Self-check an agent",
        "description": (
            "Run an iFixAi self-check against an agent's OpenAI-shaped chat endpoint and "
            "return the verdict and findings. Takes about a minute for the smoke suite. "
            "Not certified, no badge."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_url": {"type": "string", "description": "e.g. https://staging.example.com/v1"},
                "confirm_sandboxed": {
                    "type": "boolean",
                    "description": "True only if the user confirmed this URL is a test deployment with sandboxed backends and no real data.",
                },
                "suite": {"type": "string", "description": "Inspection suite (default smoke)."},
                "judge_provider": {
                    "type": "string",
                    "enum": ["anthropic", "openai", "gemini", "openrouter", "azure", "bedrock", "mock"],
                    "description": "Judge provider; uses the user's own key from the environment.",
                },
            },
            "required": ["agent_url", "confirm_sandboxed"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
    },
    {
        "name": "list_findings",
        "title": "List findings",
        "description": "The verdict and the failed inspections in the latest self-check, or in a given report.",
        "inputSchema": {
            "type": "object",
            "properties": {"report": REPORT_PROP},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "explain_failure",
        "title": "Explain a failure",
        "description": (
            "Explain in plain language why one inspection failed: what the agent was asked, "
            "what it should have done, what it did, and the controls it maps to. Built from "
            "the evidence by a template, not written by a model."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"inspection": INSPECTION_PROP, "report": REPORT_PROP},
            "required": ["inspection"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "add_regression_test",
        "title": "Add a finding as a regression test",
        "description": (
            "Pin a failed inspection in .ifixai/regressions.yaml so every later ifixai audit, "
            "CI job and pre-commit run re-checks it and fails if it fails again."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "inspection": INSPECTION_PROP,
                "report": REPORT_PROP,
                "note": {"type": "string", "description": "One line: what must not happen again."},
            },
            "required": ["inspection"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
]


class ToolError(Exception):
    """A tool could not do what was asked; the message goes back to the client."""


def log(message: str) -> None:
    print(f"[{SERVER_NAME}] {message}", file=sys.stderr, flush=True)


class Server:
    def __init__(self, root: Path, run_audit: Callable[[list[str], Path], subprocess.CompletedProcess] | None = None):
        self.root = root.resolve()
        self.run_audit = run_audit or default_run_audit

    # ---- paths -----------------------------------------------------------
    def inside(self, raw: str) -> Path:
        path = (self.root / raw).resolve()
        if path != self.root and self.root not in path.parents:
            raise ToolError(f"{raw} is outside the project folder")
        return path

    def report_path(self, raw: str | None) -> Path:
        if raw:
            return self.inside(raw)
        latest = self.root / RESULT_PATH
        if not latest.is_file():
            raise ToolError("No self-check yet in this project. Run audit_agent first, or pass report.")
        try:
            pointer = json.loads(latest.read_text(encoding="utf-8")).get("report_path")
        except (OSError, json.JSONDecodeError):
            raise ToolError(f"{RESULT_PATH} is unreadable; run audit_agent again") from None
        if not pointer:
            raise ToolError(f"{RESULT_PATH} names no report; run audit_agent again")
        return self.inside(pointer)

    def scorecard(self, raw: str | None) -> tuple[Path, dict[str, Any]]:
        path = self.report_path(raw)
        try:
            return path, load_scorecard(path)
        except SelfCheckError as exc:
            raise ToolError(str(exc)) from None

    # ---- tools -----------------------------------------------------------
    def audit_agent(self, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("confirm_sandboxed") is not True:
            raise ToolError(
                "Not run: ask the user to confirm the URL is a test deployment with sandboxed "
                "backends and no real data, then call again with confirm_sandboxed true."
            )
        argv = ["audit", str(args["agent_url"]), "--yes", "--json"]
        if args.get("suite"):
            argv += ["--suite", str(args["suite"])]
        if args.get("judge_provider"):
            argv += ["--judge-provider", str(args["judge_provider"])]
        try:
            completed = self.run_audit(argv, self.root)
        except subprocess.TimeoutExpired:
            raise ToolError(f"The self-check took longer than {AUDIT_TIMEOUT_SECONDS // 60} minutes and was stopped") from None
        stdout = completed.stdout or ""
        start = stdout.find("{")
        if start == -1:
            tail = (completed.stderr or "").strip().splitlines()[-5:]
            raise ToolError("The self-check did not finish: " + (" ".join(tail) or f"exit {completed.returncode}"))
        try:
            result = json.loads(stdout[start:])
        except json.JSONDecodeError:
            raise ToolError("The self-check printed an unreadable result") from None
        result["exit_code"] = completed.returncode
        return result

    def list_findings(self, args: dict[str, Any]) -> dict[str, Any]:
        path, card = self.scorecard(args.get("report"))
        result = build_result(card, report_path=path.relative_to(self.root).as_posix())
        return {k: result[k] for k in ("label", "certified", "badge", "verdict", "counts", "findings", "undecided", "report_path", "notice")}

    def explain_failure(self, args: dict[str, Any]) -> dict[str, Any]:
        _, card = self.scorecard(args.get("report"))
        try:
            return explain(card, str(args["inspection"]))
        except KeyError as exc:
            raise ToolError(str(exc.args[0])) from None

    def add_regression_test(self, args: dict[str, Any]) -> dict[str, Any]:
        _, card = self.scorecard(args.get("report"))
        target = self.root / regressions_mod.DEFAULT_PATH
        try:
            entry = regressions_mod.entry_from_scorecard(card, str(args["inspection"]), note=args.get("note"))
            added = regressions_mod.add(target, entry)
        except KeyError as exc:
            raise ToolError(str(exc.args[0])) from None
        except (ValueError, regressions_mod.RegressionFileError) as exc:
            raise ToolError(str(exc)) from None
        return {
            "added": added,
            "file": regressions_mod.DEFAULT_PATH.as_posix(),
            "entry": entry,
            "text": (
                f"Pinned {entry['inspection']} in {regressions_mod.DEFAULT_PATH.as_posix()}; commit it."
                if added else f"{entry['inspection']} was already pinned."
            ),
            "notice": NOTICE,
        }

    # ---- protocol --------------------------------------------------------
    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = {
            "audit_agent": self.audit_agent,
            "list_findings": self.list_findings,
            "explain_failure": self.explain_failure,
            "add_regression_test": self.add_regression_test,
        }.get(name)
        if handler is None:
            raise KeyError(name)
        tool = next(t for t in TOOLS if t["name"] == name)
        missing = [k for k in tool["inputSchema"].get("required", []) if k not in args]
        if missing:
            return tool_result({"error": f"missing argument(s): {', '.join(missing)}"}, error=True)
        try:
            return tool_result(handler(args))
        except ToolError as exc:
            return tool_result({"error": str(exc)}, error=True)

    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return error_response(None, -32600, "Invalid Request")
        method = message.get("method")
        msg_id = message.get("id")
        is_notification = "id" not in message
        params = message.get("params") or {}
        if not isinstance(method, str):
            return None if is_notification else error_response(msg_id, -32600, "Invalid Request")
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            body: dict[str, Any] = {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": "iFixAi self-check", "version": VERSION},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            body = {}
        elif method == "tools/list":
            body = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments")
            args = {} if args is None else args
            if not isinstance(args, dict):
                return error_response(msg_id, -32602, "arguments must be an object")
            try:
                body = self.call_tool(str(name), args)
            except KeyError:
                return error_response(msg_id, -32602, f"Unknown tool: {name}")
        else:
            return None if is_notification else error_response(msg_id, -32601, f"Method not found: {method}")
        return None if is_notification else {"jsonrpc": "2.0", "id": msg_id, "result": body}

    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        log(f"serving {self.root}")
        for line in stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response: dict[str, Any] | None = error_response(None, -32700, "Parse error")
            else:
                try:
                    response = self.handle(message)
                except Exception as exc:  # noqa: BLE001 - one bad call must not end the session
                    log(f"internal error: {exc!r}")
                    msg_id = message.get("id") if isinstance(message, dict) else None
                    response = error_response(msg_id, -32603, "Internal error")
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()


def tool_result(document: dict[str, Any], *, error: bool = False) -> dict[str, Any]:
    text = document.get("text") or document.get("verdict") or document.get("error") or json.dumps(document)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": document,
        "isError": error,
    }


def error_response(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def default_run_audit(argv: list[str], root: Path) -> subprocess.CompletedProcess:
    # Fixed interpreter and module; the arguments are a list, never a shell string.
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "ifixai.cli.main", *argv],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=AUDIT_TIMEOUT_SECONDS,
        check=False,
    )
