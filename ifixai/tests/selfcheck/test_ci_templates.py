"""The CI surfaces that ship with the self-check: GitHub Action, GitLab, pre-commit."""

from __future__ import annotations

import pathlib

import pytest
import yaml
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli

REPO = pathlib.Path(__file__).resolve().parents[3]
requires_checkout = pytest.mark.skipif(
    not (REPO / "pyproject.toml").is_file(), reason="installed package: no repository to inspect"
)


@requires_checkout
def test_self_check_action_is_wired() -> None:
    action = yaml.safe_load((REPO / "actions/self-check/action.yml").read_text(encoding="utf-8"))
    assert action["inputs"]["agent-url"]["required"] is True
    for name in ("confirm-sandboxed", "upload-sarif", "comment", "fail-on", "version"):
        assert name in action["inputs"], name
    steps = {step["name"]: step for step in action["runs"]["steps"]}
    upload = steps["Upload findings to code scanning"]
    assert upload["uses"].startswith("github/codeql-action/upload-sarif@")
    run = steps["Self-check"]["run"]
    assert "--yes" in run and "INPUT_CONFIRM_SANDBOXED" in run
    for step in action["runs"]["steps"]:
        assert "${{" not in step.get("run", ""), step["name"]


@requires_checkout
def test_gitlab_template_parses_and_confirms_sandbox() -> None:
    template = yaml.safe_load((REPO / "ci/gitlab/ifixai-self-check.gitlab-ci.yml").read_text(encoding="utf-8"))
    job = template["ifixai-self-check"]
    script = "\n".join(job["script"])
    assert "ifixai \"$@\"" in script and "--yes" in script
    assert template["variables"]["IFIXAI_CONFIRM_SANDBOXED"] == "false"
    assert ".ifixai/self-check/" in job["artifacts"]["paths"]


@requires_checkout
def test_pre_commit_hooks() -> None:
    hooks = {h["id"]: h for h in yaml.safe_load((REPO / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))}
    assert hooks["ifixai-regressions"]["entry"] == "ifixai findings validate-regressions"
    assert hooks["ifixai-self-check"]["stages"] == ["pre-push"]


def test_validate_regressions_command(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert "0 pinned" in runner.invoke(ifixai_cli, ["findings", "validate-regressions"]).output
    good = tmp_path / "r.yaml"
    good.write_text("version: 1\nregressions:\n  - inspection: B08\n  - hosted: {run_id: r, finding: f}\n", encoding="utf-8")
    result = runner.invoke(ifixai_cli, ["findings", "validate-regressions", str(good)])
    assert result.exit_code == 0 and "1 pinned inspection(s), 1 hosted" in result.output
    good.write_text("version: 3\n", encoding="utf-8")
    assert runner.invoke(ifixai_cli, ["findings", "validate-regressions", str(good)]).exit_code == 1
