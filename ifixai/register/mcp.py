"""An MCP server so an agent in Claude Code or Cursor can *prepare* a register submission.

The reason this exists: the moment a developer's agent notices misbehaviour is the moment the
report is cheapest to write and most likely to be written. The reason it is careful: that same
agent is the last thing that should be able to publish a company's data.

So the tools here prepare and nothing else. There is no ``submit`` tool, and there cannot be one by
accident: this module imports :mod:`ifixai.register.proposal`, which has no network capability,
and never imports :mod:`ifixai.register.submit`, which does. Every tool's result ends with the
same handoff — the exact command the human runs to see the side-by-side and approve. The agent
writes the report; the person publishes it, or does not.

``ifixai/tests/register/test_human_gate.py`` asserts the import graph, the absence of a sending
tool, and that every tool result carries the handoff.

Transport is JSON-RPC 2.0 over stdio, hand-rolled on the standard library. No new dependency and
no new toolchain: the project already ships a plugin for these editors (``plugin/``), and this is
one more process they can launch.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, TextIO

import yaml

from ifixai._version import VERSION
from ifixai.register import diff, proposal, scrub, signature, store, validator
from ifixai.register.spec import SPEC_VERSION, MisbehaviourReport

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ifixai-register"

#: Appended to every tool result. The agent is told, in the result it reads, that it cannot finish
#: this job — so an agent following the result rather than the docs still stops here.
HANDOFF = (
    "PREPARED, NOT SENT. Nothing has left this machine. This tool cannot submit, and no tool on this "
    "server can. To file it, the person at the keyboard must run:\n"
    "    ifixai report submit {path}\n"
    "which shows them the original beside the redacted version, asks them to confirm the de-identified "
    "and rights-cleared flags, and asks them to type the confirmation word. Do not attempt to run `git`, "
    "`gh` or any network command to publish this yourself; ask the person to run the command above."
)


class ToolError(RuntimeError):
    """A tool could not do what was asked. Returned to the agent as an error result, never raised
    out of the server loop."""


def _result(text: str, path: str = "<the draft you saved>") -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"{text}\n\n{HANDOFF.format(path=path)}"}]}


def _load(payload: Any) -> MisbehaviourReport:
    if isinstance(payload, str):
        try:
            payload = yaml.safe_load(payload)
        except yaml.YAMLError as error:
            raise ToolError(f"the report is not valid YAML or JSON: {error}") from None
    if not isinstance(payload, dict):
        raise ToolError("`report` must be a mapping, or a YAML/JSON string holding one")
    checked = validator.validate_payload(payload)
    if checked.report is None:
        raise ToolError(f"the report does not match the spec:\n{checked.render()}")
    return checked.report


# -- the tools ---------------------------------------------------------------------------


def tool_describe_spec(_: dict[str, Any]) -> dict[str, Any]:
    """Hand the agent the format, so it writes a valid report the first time."""
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "misbehaviour-report-1.schema.json"
    schema = schema_path.read_text(encoding="utf-8") if schema_path.exists() else "{}"
    return _result(
        f"iFixAi misbehaviour report, spec version {SPEC_VERSION}.\n\n"
        "Required, each because omitting it breaks reproduction: world state (concrete ids and field "
        "values), identity and authorization (roles, which role holds which tool, escalation paths; "
        "identity comes from the login and is never claimable in conversation), tools (name, signature, "
        "risk, whether it mutates state), the request (verbatim, and which login sent it), expected "
        "behaviour (a condition on the FINAL WORLD STATE, never on the wording of a reply), the twin (the "
        "identical request about the same record from a role that legitimately holds the tool — without it "
        "an agent that refuses everything scores perfectly), the grading contract (named 0/1 checks over "
        "world state or the evidence log, never an LLM judge), calibration evidence (reference solution "
        "scores 1, doing nothing scores 0), the determinism declaration (seed, temperature, step cap, what "
        "may vary), the observed result (model and version, harness and version, occurrences out of "
        "trials as counts, date), and provenance and rights (synthetic or audit-derived; de-identified "
        "and rights-cleared flags, both required for audit-derived).\n\n"
        "The format is runtime-neutral: describe the behaviour in the report's own terms, then bind it to "
        "a runtime under `reproductions`. Harbor is the reference implementation, not the definition.\n\n"
        f"JSON Schema:\n```json\n{schema}\n```"
    )


def tool_compute_signature(arguments: dict[str, Any]) -> dict[str, Any]:
    """The behaviour signature and whether the register already knows it."""
    from ifixai.register.spec import BehaviourSignature

    try:
        sig = BehaviourSignature(
            family=str(arguments["family"]),
            failure=str(arguments["failure"]),
            trigger=str(arguments["trigger"]),
        )
    except KeyError as error:
        raise ToolError(f"missing argument: {error.args[0]}") from None
    digest = signature.digest(sig)
    register = store.load_register(Path(str(arguments.get("register_root", "."))))
    hits = [
        report.id or report.candidate_ref
        for report in register.reports
        if signature.digest(report.signature) == digest
    ]
    known = (
        "This behaviour is already filed as: " + ", ".join(str(hit) for hit in hits)
        if hits
        else "No report in the register has this signature. If a run showed it, it is worth filing."
    )
    return _result(f"Signature digest `{digest}` for ({sig.family}, {sig.failure}, {sig.trigger}).\n\n{known}")


def tool_prepare_submission(arguments: dict[str, Any]) -> dict[str, Any]:
    """De-identify, validate, de-duplicate and write the draft to disk. Sends nothing."""
    report = _load(arguments.get("report"))
    scrubbed = scrub.scrub(report, tuple(str(name) for name in arguments.get("extra_names", ())))
    root = Path(str(arguments.get("register_root", ".")))
    register = store.load_register(root)
    prepared = proposal.prepare(scrubbed.report, register.reports)
    out = Path(str(arguments.get("out", "register-submission.yaml")))
    out.write_text(prepared.yaml(), encoding="utf-8")
    lines = [
        f"Prepared a candidate for review and saved it to {out}.",
        "",
        f"Signature: `{scrubbed.report.signature.digest}`",
        f"Validation: {'passes' if prepared.validation.ok else 'FAILS'}",
    ]
    if not prepared.validation.ok:
        lines += ["", prepared.validation.render()]
    if prepared.duplicates:
        lines += ["", "Possible duplicate(s):"] + [f"  - {dup}" for dup in prepared.duplicates]
    lines += ["", f"De-identification: {diff.summary(scrubbed.findings)}"]
    if scrubbed.findings:
        lines += [
            "",
            "The de-identifier replaced the values above locally. You have not been shown the originals and "
            "must not ask for them: the person at the keyboard reviews them in the side-by-side.",
        ]
    return _result("\n".join(lines), path=str(out))


def tool_validate_report(arguments: dict[str, Any]) -> dict[str, Any]:
    """Check a report without preparing anything."""
    payload = arguments.get("report")
    if isinstance(payload, str):
        try:
            payload = yaml.safe_load(payload)
        except yaml.YAMLError as error:
            raise ToolError(f"not valid YAML or JSON: {error}") from None
    result = validator.validate_payload(payload)
    if result.ok:
        return _result("The report conforms to the spec and passes every rule.")
    return _result(f"The report does not pass:\n{result.render()}")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]


REPORT_ARGUMENT = {
    "type": ["object", "string"],
    "description": "The report, as a mapping or as a YAML/JSON string.",
}

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="register_describe_spec",
        description=(
            "The misbehaviour report format: every required field, why it is required, and the JSON Schema. "
            "Read this before writing a report."
        ),
        schema={"type": "object", "properties": {}},
        handler=tool_describe_spec,
    ),
    Tool(
        name="register_compute_signature",
        description=(
            "The behaviour signature for a (family, failure, trigger) triple, and whether the register "
            "already holds a report with it. Use it to tell a new behaviour from a known one."
        ),
        schema={
            "type": "object",
            "properties": {
                "family": {"type": "string"},
                "failure": {"type": "string"},
                "trigger": {"type": "string"},
                "register_root": {"type": "string", "description": "Checkout holding register/ (default .)"},
            },
            "required": ["family", "failure", "trigger"],
        },
        handler=tool_compute_signature,
    ),
    Tool(
        name="register_validate_report",
        description="Check a report against the spec and report every problem, changing nothing.",
        schema={"type": "object", "properties": {"report": REPORT_ARGUMENT}, "required": ["report"]},
        handler=tool_validate_report,
    ),
    Tool(
        name="register_prepare_submission",
        description=(
            "De-identify a report locally, validate it, flag duplicates and save it for the human to "
            "review. This PREPARES ONLY. It cannot and will not send anything: the person at the keyboard "
            "must run `ifixai report submit` to see the redaction side by side and approve it."
        ),
        schema={
            "type": "object",
            "properties": {
                "report": REPORT_ARGUMENT,
                "extra_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Real names to strike from free text as well as structured fields.",
                },
                "out": {"type": "string", "description": "Where to save the draft (default register-submission.yaml)"},
                "register_root": {"type": "string"},
            },
            "required": ["report"],
        },
        handler=tool_prepare_submission,
    ),
)

BY_NAME = {tool.name: tool for tool in TOOLS}


# -- the server loop ---------------------------------------------------------------------


def handle(message: dict[str, Any]) -> Optional[dict[str, Any]]:
    """One JSON-RPC request in, one response out (or None for a notification)."""
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        body: dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": VERSION},
            "instructions": (
                "Tools for filing a reproducible AI-agent misbehaviour report. Everything here prepares; "
                "nothing here publishes. Submission needs the person at the keyboard."
            ),
        }
    elif method == "tools/list":
        body = {
            "tools": [
                {"name": tool.name, "description": tool.description, "inputSchema": tool.schema} for tool in TOOLS
            ]
        }
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        tool = BY_NAME.get(str(name))
        if tool is None:
            return _error(request_id, -32601, f"no such tool: {name}")
        try:
            body = tool.handler(params.get("arguments") or {})
        except ToolError as error:
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": str(error)}], "isError": True}}
        except Exception as error:  # noqa: BLE001 - a tool crash is reported to the agent, never fatal
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"{type(error).__name__}: {error}"}],
                    "isError": True,
                },
            }
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return None
    elif method == "ping":
        body = {}
    else:
        if request_id is None:
            return None
        return _error(request_id, -32601, f"method not supported: {method}")
    return {"jsonrpc": "2.0", "id": request_id, "result": body}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            sink.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            sink.flush()
            continue
        response = handle(message)
        if response is None:
            continue
        sink.write(json.dumps(response) + "\n")
        sink.flush()
