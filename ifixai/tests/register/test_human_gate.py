"""**No submission leaves a machine without an explicit per-submission human yes.**

This is the constraint that outranks everything else in the contribution path, so it is asserted
from several directions rather than once:

* ``submit`` refuses without an approval, and refuses an approval that does not match the exact
  bytes being sent (so an approval cannot be replayed onto an edited report).
* an ``Approval`` cannot be built without a ``HumanWitness``, and a ``HumanWitness`` comes only from
  ``confirm``, which reads typed answers.
* ``confirm`` refuses on anything short of a yes to both flags and the confirmation word typed
  exactly.
* the MCP surface an agent talks to has no submit tool, and no import path to the module that can
  reach the network.
* the source of the whole package contains none of the shapes a bypass would take.

The last two are the ones that protect this property against a future change rather than against
today's code.
"""

from __future__ import annotations

import ast
import io
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from ifixai.register import approval as approval_module
from ifixai.register import mcp, proposal, submit
from ifixai.register.approval import (
    AFFIRMATIVE,
    Approval,
    ApprovalDeclined,
    ApprovalRequired,
    HumanWitness,
    confirm,
    payload_digest,
)
from ifixai.register.clearance import Clearance
from ifixai.register.spec import MisbehaviourReport

pytestmark = pytest.mark.unit

PACKAGE = Path(approval_module.__file__).parent

#: The four tools the server exposes. An exact set, not a pattern: the property being asserted is
#: that nothing else is there, and a new tool has to be added here deliberately.
EXPECTED_TOOLS = {
    "register_describe_spec",
    "register_compute_signature",
    "register_validate_report",
    "register_prepare_submission",
}


def code_only(path: Path) -> str:
    """A module's code with every docstring and comment removed.

    These tests assert on what the code does, and several of the modules under test *discuss* the
    things being forbidden — ``approval.py``'s own docstring lists the bypass shapes in order to say
    they are refused, and ``mcp.py``'s explains that it never imports the sending module. Grepping
    raw source would fail on the explanation rather than on a violation, so prose is stripped first.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _answers(*values: str) -> Callable[[], str]:
    queue: list[str] = list(values)

    def read() -> str:
        return queue.pop(0) if queue else ""

    return read


def _approve(report: MisbehaviourReport) -> Approval:
    """The two-question path: no standing clearance, so rights are asked outright."""
    return confirm(report, granted_by="a person", reader=_answers("y", "y"), writer=io.StringIO())


def _clearance(expires: date) -> Clearance:
    return Clearance(
        organisation="Acme Health Partners",
        authorised_by="R. Mbeki, Head of Platform Security",
        scope="Synthetic and de-identified findings from our own staging systems.",
        expires_on=expires,
        excludes=(),
        source_file=".ifixai/contribution.yaml",
    )


# -- submit refuses without a matching approval ------------------------------------------


def test_submit_without_approval_raises(entry: MisbehaviourReport, tmp_path: Path) -> None:
    prepared = proposal.prepare(entry)
    with pytest.raises(ApprovalRequired, match="no human approval"):
        submit.submit(prepared, None, checkout=tmp_path, dry_run=True)


def test_submit_refuses_an_approval_for_different_bytes(entry: MisbehaviourReport, tmp_path: Path) -> None:
    """Approve a report, change one character, and the approval no longer covers it. The person
    approved bytes, not an intention."""
    approval = _approve(entry)
    edited = entry.model_copy(update={"summary": entry.summary + " Also, one more thing."})
    prepared = proposal.prepare(edited)
    with pytest.raises(ApprovalRequired, match="changed after it was approved"):
        submit.submit(prepared, approval, checkout=tmp_path, dry_run=True)


def test_the_clearance_module_cannot_approve_anything() -> None:
    """The pre-clearance file answers the rights question. Nothing in it reaches the gate: it
    produces a record, never a witness."""
    from ifixai.register import clearance as clearance_mod

    source = code_only(Path(clearance_mod.__file__))
    assert "HumanWitness" not in source
    assert "Approval(" not in source


def test_submit_with_a_matching_approval_proceeds(entry: MisbehaviourReport, tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)

    prepared = proposal.prepare(entry)
    sent = submit.submit(prepared, _approve(entry), checkout=tmp_path, dry_run=True)
    assert sent.pull_request_url == ""  # dry run: nothing pushed, nothing opened
    assert (tmp_path / prepared.relative_path).exists()
    assert sent.approval["granted_by"] == "a person"
    assert sent.approval["payload_digest"] == payload_digest(entry)


def test_every_sending_function_takes_an_approval() -> None:
    """A new function in submit.py that sends has to take an approval too. This reads the module's
    own AST rather than trusting a reviewer to notice."""
    tree = ast.parse(Path(submit.__file__).read_text(encoding="utf-8"))
    senders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            source = ast.get_source_segment(Path(submit.__file__).read_text(encoding="utf-8"), node) or ""
            if "gh" in source and "pr" in source and "create" in source:
                senders.append(node)
    assert senders, "submit.py should contain the function that opens the pull request"
    for node in senders:
        arguments = [argument.arg for argument in node.args.args]
        assert "granted" in arguments, f"{node.name} can send without taking an approval"
        assert "approval_module.require" in (
            ast.get_source_segment(Path(submit.__file__).read_text(encoding="utf-8"), node) or ""
        ), f"{node.name} does not go through approval.require"


# -- an approval cannot be forged ---------------------------------------------------------


def test_approval_needs_a_human_witness(entry: MisbehaviourReport) -> None:
    with pytest.raises(ApprovalRequired, match="HumanWitness"):
        Approval(
            witness=object(),  # type: ignore[arg-type]
            payload_digest=payload_digest(entry),
            de_identified_confirmed=True,
            rights_cleared_confirmed=True,
            granted_by="nobody",
            granted_at=approval_module.datetime.now(approval_module.timezone.utc),
        )


def test_a_witness_for_other_bytes_is_refused(entry: MisbehaviourReport) -> None:
    now = approval_module.datetime.now(approval_module.timezone.utc)
    witness = HumanWitness(digest="0" * 64, answered_at=now)
    with pytest.raises(ApprovalRequired, match="different bytes"):
        Approval(
            witness=witness,
            payload_digest=payload_digest(entry),
            de_identified_confirmed=True,
            rights_cleared_confirmed=True,
            granted_by="a person",
            granted_at=now,
        )


def test_an_approval_without_both_flags_is_refused(entry: MisbehaviourReport) -> None:
    now = approval_module.datetime.now(approval_module.timezone.utc)
    digest = payload_digest(entry)
    with pytest.raises(ApprovalDeclined, match="rights-cleared"):
        Approval(
            witness=HumanWitness(digest=digest, answered_at=now),
            payload_digest=digest,
            de_identified_confirmed=True,
            rights_cleared_confirmed=False,
            granted_by="a person",
            granted_at=now,
        )


@pytest.mark.parametrize(
    "answers",
    [
        ("n", "y"),
        ("", ""),
        ("y", "n"),
        ("y", ""),
        ("no", "y"),
        ("yeah", "y"),
    ],
)
def test_confirm_refuses_anything_short_of_a_yes(entry: MisbehaviourReport, answers) -> None:
    with pytest.raises(ApprovalDeclined):
        confirm(entry, granted_by="a person", reader=_answers(*answers), writer=io.StringIO())


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES"])
def test_the_affirmative_answers_are_exactly_these(entry: MisbehaviourReport, answer: str) -> None:
    approval = confirm(entry, granted_by="a person", reader=_answers(answer, "y"), writer=io.StringIO())
    assert approval.covers(entry)
    assert answer.lower() in AFFIRMATIVE


def test_the_question_is_asked_under_the_factual_context(entry: MisbehaviourReport) -> None:
    shown = io.StringIO()
    confirm(entry, granted_by="a person", reader=_answers("y", "y"), writer=shown)
    asked = shown.getvalue()
    assert "aren't published anywhere today" in asked
    assert "Contribute to the public catalogue? [y/N]" in asked
    assert "entitled to publish" in asked


def test_the_context_is_factual_not_persuasive() -> None:
    """Two short lines, stating the gap and the consequence. Nothing that reads as a pitch: these
    are engineers running a security tool, and a prompt that sells makes them doubt the
    de-identification promise, which is the one thing it cannot afford."""
    block = approval_module.context_block()
    assert len(block.splitlines()) <= 2
    lowered = block.lower()
    for word in (
        "help",
        "community",
        "join",
        "mission",
        "safer",
        "together",
        "important",
        "urgent",
        "please",
        "!",
        "thousands",
        "contributors",
    ):
        assert word not in lowered, f"the context line reads as persuasion: {word!r}"


def test_the_prompts_default_to_no(entry: MisbehaviourReport) -> None:
    """A prompt that defaults to yes is a prompt people clear without reading."""
    for prompt in (approval_module.CONTRIBUTE_PROMPT, approval_module.RIGHTS_PROMPT):
        assert prompt.rstrip().endswith("[y/N]")


def test_a_valid_standing_clearance_makes_it_one_question(entry: MisbehaviourReport) -> None:
    """One keystroke, and the rights answer is traceable to the person who gave it."""
    shown = io.StringIO()
    reader = _answers("y")  # a second question would raise IndexError... or read ""
    approval = confirm(
        entry,
        granted_by="a person",
        clearance=_clearance(date(2027, 3, 31)),
        reader=reader,
        writer=shown,
        today=date(2026, 11, 12),
    )
    assert approval.rights_cleared_confirmed
    assert approval.pre_clearance is not None
    assert approval.pre_clearance.authorised_by == "R. Mbeki, Head of Platform Security"
    assert "entitled to publish" not in shown.getvalue()
    assert shown.getvalue().count("[y/N]") == 1


def test_an_expired_clearance_falls_back_to_asking(entry: MisbehaviourReport) -> None:
    shown = io.StringIO()
    approval = confirm(
        entry,
        granted_by="a person",
        clearance=_clearance(date(2026, 1, 1)),
        reader=_answers("y", "y"),
        writer=shown,
        today=date(2026, 11, 12),
    )
    assert "entitled to publish" in shown.getvalue()
    assert approval.pre_clearance is None


def test_a_clearance_cannot_supply_the_yes(entry: MisbehaviourReport) -> None:
    """A standing clearance answers the rights question in advance. It cannot answer the one that
    matters, and a declined contribution with a perfectly valid clearance still sends nothing."""
    with pytest.raises(ApprovalDeclined):
        confirm(
            entry,
            granted_by="a person",
            clearance=_clearance(date(2027, 3, 31)),
            reader=_answers("n"),
            writer=io.StringIO(),
            today=date(2026, 11, 12),
        )


def test_an_approval_records_who_without_recording_their_console(entry: MisbehaviourReport) -> None:
    record = approval_module.record(_approve(entry))
    assert record["granted_by"] == "a person"
    assert "answers" not in record
    assert "witness" not in record


def test_a_pre_cleared_approval_records_the_authorisation_trail(entry: MisbehaviourReport) -> None:
    approval = confirm(
        entry,
        granted_by="a person",
        clearance=_clearance(date(2027, 3, 31)),
        reader=_answers("y"),
        writer=io.StringIO(),
        today=date(2026, 11, 12),
    )
    record = approval_module.record(approval)
    assert record["pre_clearance"]["authorised_by"] == "R. Mbeki, Head of Platform Security"
    assert record["pre_clearance"]["expires_on"] == "2027-03-31"
    assert record["pre_clearance"]["source_file"] == ".ifixai/contribution.yaml"


# -- the agent-facing surface cannot send -------------------------------------------------


def test_the_mcp_server_exposes_only_prepare_only_tools() -> None:
    names = {tool.name for tool in mcp.TOOLS}
    assert names == EXPECTED_TOOLS, f"the server's tool set changed: {sorted(names)}"
    for tool in mcp.TOOLS:
        handler = code_only(Path(mcp.__file__))
        assert tool.handler.__name__ in handler


def test_the_mcp_module_cannot_reach_the_network() -> None:
    """A structural guarantee, not a docstring: mcp.py imports `proposal`, which has no network
    capability, and never imports `submit`, which does."""
    source = code_only(Path(mcp.__file__))
    tree = ast.parse(Path(mcp.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(f"{node.module}.{alias.name}")
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    assert not any("submit" in name for name in imported), f"mcp imports the sending module: {imported}"
    for forbidden in ("subprocess", "socket", "http", "urllib", "requests", "aiohttp"):
        assert forbidden not in imported, f"mcp imports {forbidden}"
    assert "ifixai.register.submit" not in source


def test_proposal_has_no_network_capability() -> None:
    source = code_only(Path(proposal.__file__))
    for forbidden in ("subprocess", "socket", "urllib", "requests", "aiohttp"):
        assert forbidden not in source, f"proposal.py uses {forbidden}"


def test_every_mcp_tool_result_tells_the_agent_it_cannot_finish(tmp_path: Path) -> None:
    for tool in mcp.TOOLS:
        if tool.name == "register_describe_spec":
            result = tool.handler({})
        elif tool.name == "register_compute_signature":
            result = tool.handler(
                {"family": "f", "failure": "x", "trigger": "t", "register_root": str(tmp_path)}
            )
        else:
            continue
        text = result["content"][0]["text"]
        assert "PREPARED, NOT SENT" in text
        assert "ifixai report submit" in text
        assert "cannot submit" in text


# -- no bypass, now or later --------------------------------------------------------------

#: The shapes a bypass would take. ``approval.py`` itself is exempt where it names them in order to
#: refuse them, and the test files are exempt because they assert on them.
BYPASS_SHAPES = (
    r"--yes\b",
    r"\bassume_yes\b",
    r"\bskip_approval\b",
    r"\bno_confirm\b",
    r"\bauto_submit\b",
    r"\bforce\s*=\s*True\b",
    r"IFIXAI_[A-Z_]*(?:APPROVE|CONFIRM|YES|AUTO)",
)


@pytest.mark.parametrize("shape", BYPASS_SHAPES)
def test_no_bypass_anywhere_in_the_register_package(shape: str) -> None:
    pattern = re.compile(shape)
    for path in sorted(PACKAGE.glob("*.py")):
        assert not pattern.search(code_only(path)), (
            f"{path.name} contains a possible approval bypass: {shape}"
        )


def test_the_cli_submit_path_reads_no_environment_variable() -> None:
    """An environment variable that stood in for the human would be a config flag by another name."""
    from ifixai.cli import report as report_cli

    source = code_only(Path(report_cli.__file__))
    assert "os.environ" not in source
    assert "getenv" not in source


def test_submit_calls_require_before_anything_else() -> None:
    """Ordering matters: a check after the first side effect is not a gate."""
    source = Path(submit.__file__).read_text(encoding="utf-8")
    body = source[source.index("def submit("):]
    statements = body[body.index('"""', body.index('"""') + 3) + 3 :]
    first = next(line.strip() for line in statements.splitlines() if line.strip())
    assert "approval_module.require" in first, f"the first statement of submit() is {first!r}"
