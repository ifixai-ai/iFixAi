"""Synthetic end-to-end regressions for submission's local safety boundaries."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from ifixai.register import mcp, proposal, scrub, store, submit
from ifixai.register.approval import confirm
from ifixai.register.spec import MisbehaviourReport

pytestmark = pytest.mark.unit


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, text=True, capture_output=True
    ).stdout


def test_submit_preserves_unrelated_index_and_worktree(
    entry: MisbehaviourReport, tmp_path: Path
) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Synthetic test")
    other = tmp_path / "unrelated.txt"
    other.write_text("original\n")
    _git(tmp_path, "add", "--", other.name)
    _git(tmp_path, "commit", "-qm", "seed")
    other.write_text("staged edit\n")
    _git(tmp_path, "add", "--", other.name)
    other.write_text("unstaged edit\n")
    staged = _git(tmp_path, "diff", "--cached", "--binary")
    working = _git(tmp_path, "diff", "--binary")
    answers = iter(("y", "y"))
    approval = confirm(
        entry,
        granted_by="synthetic test",
        reader=lambda: next(answers),
        writer=io.StringIO(),
    )
    prepared = proposal.prepare(entry)
    submit.submit(prepared, approval, checkout=tmp_path, dry_run=True)
    assert _git(
        tmp_path, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
    ).splitlines() == [prepared.relative_path]
    assert _git(tmp_path, "diff", "--cached", "--binary") == staged
    assert _git(tmp_path, "diff", "--binary") == working
    assert other.read_text() == "unstaged edit\n"


@pytest.mark.parametrize("key", ["id", "source", "status", "version", "model"])
def test_sensitive_values_are_scrubbed_even_under_metadata_keys(
    entry: MisbehaviourReport, key: str
) -> None:
    payload = entry.model_dump(mode="json")
    token = "sk-" + "synthetic_only_" * 3
    email = "private.person@example.invalid"
    payload["world"]["records"][0]["fields"][key] = {"nested": email + " " + token}
    result = scrub.scrub(MisbehaviourReport.model_validate(payload))
    serialized = store.dump_yaml(result.report)
    assert email not in serialized
    assert token not in serialized
    assert result.report.id == entry.id
    assert result.report.state == entry.state
    assert result.report.spec_version == entry.spec_version
    assert result.report.signature == entry.signature


@pytest.mark.parametrize("key_type", ["", "RSA ", "EC ", "OPENSSH ", "ENCRYPTED "])
@pytest.mark.parametrize("terminated", [False, True])
@pytest.mark.parametrize("newline", ["\r\n", r"\n"])
def test_private_key_body_is_removed(
    entry: MisbehaviourReport, key_type: str, terminated: bool, newline: str
) -> None:
    body = "SYNTHETIC_PRIVATE_MATERIAL_NOT_A_REAL_KEY"
    pem = "-----BEGIN " + key_type + "PRIVATE KEY-----" + newline + body + newline
    if terminated:
        pem += "-----END " + key_type + "PRIVATE KEY-----"
    payload = entry.model_dump(mode="json")
    payload["world"]["records"][0]["fields"]["note"] = (
        "before\n" + pem + ("\nafter" if terminated else "")
    )
    result = scrub.scrub(MisbehaviourReport.model_validate(payload))
    serialized = store.dump_yaml(result.report)
    assert body not in serialized
    assert "BEGIN " not in serialized
    assert "END " not in serialized
    assert "PRIVATE_KEY_" in serialized
    assert "before" in serialized
    if terminated:
        assert "after" in serialized


@pytest.mark.parametrize(
    "kind", ["absolute", "traversal", "parent-symlink", "file-symlink", "existing"]
)
def test_mcp_output_cannot_escape_or_overwrite(
    entry: MisbehaviourReport,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    outside = tmp_path / "outside.yaml"
    outside.write_text("preserve outside\n")
    if kind == "absolute":
        out = str(outside)
    elif kind == "traversal":
        out = "../outside.yaml"
    elif kind == "parent-symlink":
        (project / "linked").symlink_to(tmp_path, target_is_directory=True)
        out = "linked/outside.yaml"
    elif kind == "file-symlink":
        (project / "linked.yaml").symlink_to(outside)
        out = "linked.yaml"
    else:
        out = "existing.yaml"
        (project / out).write_text("preserve inside\n")
    response = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "register_prepare_submission",
                "arguments": {
                    "report": entry.model_dump(mode="json"),
                    "out": out,
                    "register_root": str(tmp_path),
                },
            },
        }
    )
    assert response is not None and response["result"].get("isError") is True
    assert outside.read_text() == "preserve outside\n"
    if kind == "existing":
        assert (project / out).read_text() == "preserve inside\n"


def test_mcp_creates_relative_draft_inside_project(
    entry: MisbehaviourReport, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = mcp.tool_prepare_submission(
        {"report": entry.model_dump(mode="json"), "out": "draft.yaml"}
    )
    assert not result.get("isError")
    assert store.load_report(tmp_path / "draft.yaml")
