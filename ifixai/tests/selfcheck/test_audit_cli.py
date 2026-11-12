"""`ifixai audit` and `ifixai findings`, with the run itself stubbed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ifixai.cli import audit as audit_mod
from ifixai.cli.main import ifixai_cli
from ifixai.harness.suites import resolve_suite
from ifixai.providers.resolver import _PROVIDER_CREDENTIAL_ENV_VARS as PROVIDER_ENV
from ifixai.selfcheck import regressions
from ifixai.tests.selfcheck.scorecards import scorecard, write

URL = "https://agent.example/v1"


@pytest.fixture
def runner(tmp_path, monkeypatch) -> CliRunner:
    monkeypatch.chdir(tmp_path)
    # Every credential variable the resolver reads, so no real key can pair a judge.
    names = {name for group in PROVIDER_ENV.values() for name in group}
    for var in sorted(names | {audit_mod.AGENT_KEY_ENV}):
        monkeypatch.delenv(var, raising=False)
    return CliRunner()


@pytest.fixture
def fake_run(monkeypatch):
    calls: list[dict[str, Any]] = []
    state: dict[str, Any] = {"card": scorecard(), "code": 0, "write": True}

    def fake(ctx, **params):
        calls.append(params)
        if state["write"]:
            write(Path(params["output"]) / "ifixai-self-check-x.json", state["card"])
        return state["code"]

    monkeypatch.setattr(audit_mod, "invoke_run", fake)
    return calls, state


def test_audit_runs_http_with_smoke_and_writes_outputs(runner, fake_run) -> None:
    calls, _ = fake_run
    result = runner.invoke(ifixai_cli, ["audit", URL, "-y", "--sarif", "out.sarif", "--comment", "c.md"])
    assert result.exit_code == 1, result.output
    [params] = calls
    assert params["provider"] == "http" and params["endpoint"] == URL
    assert params["eval_mode"] == "deterministic" and params["judge_provider"] == ()
    assert params["target_is_sandboxed"] is True and params["report_format"] == "json"
    assert params["test"] == tuple(resolve_suite("smoke")["test_ids"])
    assert "Judge: none" in result.output
    assert "Self-check, not certified: 1 of 4 checks failed" in result.output
    document = json.loads(Path(".ifixai/self-check/self-check.json").read_text(encoding="utf-8"))
    assert document["certified"] is False and document["badge"] is None
    assert document["target"] == URL
    sarif = json.loads(Path("out.sarif").read_text(encoding="utf-8"))
    assert sarif["runs"][0]["results"][0]["ruleId"] == "B08"
    assert Path("c.md").read_text(encoding="utf-8").startswith("<!-- ifixai-self-check -->")


def test_clean_audit_exits_zero_and_json_flag(runner, fake_run) -> None:
    _, state = fake_run
    state["card"] = scorecard({"B08": "pass", "B20": "pass"})
    result = runner.invoke(ifixai_cli, ["audit", URL, "-y", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output[result.output.index("{"):])
    assert payload["label"] == "self-check"


def test_nothing_decided_exits_two(runner, fake_run) -> None:
    _, state = fake_run
    state["card"] = scorecard({"B08": "inconclusive", "B12": "error", "B31": "inconclusive"})
    result = runner.invoke(ifixai_cli, ["audit", URL, "-y"])
    assert result.exit_code == 2, result.output
    assert "no verdict" in result.output


def test_no_report_exits_two(runner, fake_run) -> None:
    _, state = fake_run
    state["write"], state["code"] = False, 1
    result = runner.invoke(ifixai_cli, ["audit", URL, "-y"])
    assert result.exit_code == 2
    assert "did not produce a report" in result.output


def test_fail_on_modes(runner, fake_run) -> None:
    assert runner.invoke(ifixai_cli, ["audit", URL, "-y", "--fail-on", "never"]).exit_code == 0
    assert runner.invoke(ifixai_cli, ["audit", URL, "-y", "--fail-on", "regressions"]).exit_code == 0


def test_pinned_regressions_are_added_and_gate(runner, fake_run) -> None:
    calls, _ = fake_run
    regressions.save(regressions.DEFAULT_PATH, [{"inspection": "P01"}, {"inspection": "B08"}])
    result = runner.invoke(ifixai_cli, ["audit", URL, "-y", "--fail-on", "regressions"])
    assert result.exit_code == 1, result.output
    assert "P01" in calls[0]["test"]
    assert "+ 2 pinned" in result.output
    assert "Pinned regressions failed again: B08" in result.output


def test_judge_choice_and_env_pairing(runner, fake_run, monkeypatch) -> None:
    calls, _ = fake_run
    runner.invoke(ifixai_cli, ["audit", URL, "-y", "--judge-provider", "mock", "--judge-model", "m"])
    assert calls[-1]["eval_mode"] == "single"
    assert calls[-1]["judge_provider"] == ("mock",) and calls[-1]["judge_model"] == ("m",)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    runner.invoke(ifixai_cli, ["audit", URL, "-y"])
    assert calls[-1]["eval_mode"] is None and calls[-1]["judge_provider"] == ()


def test_agent_key_from_env(runner, fake_run, monkeypatch) -> None:
    calls, _ = fake_run
    runner.invoke(ifixai_cli, ["audit", URL, "-y"])
    assert calls[-1]["api_key"] == "unused"
    monkeypatch.setenv(audit_mod.AGENT_KEY_ENV, "agent-test-key")
    runner.invoke(ifixai_cli, ["audit", URL, "-y"])
    assert calls[-1]["api_key"] == "agent-test-key"


@pytest.mark.parametrize("url", ["ftp://x/y", "not a url", "https://user:pw@agent.example/v1"])
def test_bad_urls_are_refused_before_running(runner, fake_run, url) -> None:
    calls, _ = fake_run
    result = runner.invoke(ifixai_cli, ["audit", url, "-y"])
    assert result.exit_code == 2 and not calls


def test_unknown_suite_and_bad_regressions_file(runner, fake_run) -> None:
    calls, _ = fake_run
    assert runner.invoke(ifixai_cli, ["audit", URL, "-y", "--suite", "nope"]).exit_code == 2
    Path(".ifixai").mkdir()
    Path(".ifixai/regressions.yaml").write_text("version: 9\n", encoding="utf-8")
    result = runner.invoke(ifixai_cli, ["audit", URL, "-y"])
    assert result.exit_code == 1 and "version 9" in result.output
    assert not calls


def test_findings_commands(runner, tmp_path) -> None:
    report = str(write(tmp_path / "report.json", scorecard()))
    explained = runner.invoke(ifixai_cli, ["findings", "explain", report, "B08"])
    assert explained.exit_code == 0 and "What it did instead" in explained.output
    as_json = runner.invoke(ifixai_cli, ["findings", "explain", report, "B08", "--json"])
    assert json.loads(as_json.output)["failed"] is True
    assert runner.invoke(ifixai_cli, ["findings", "explain", report, "Z99"]).exit_code == 1

    added = runner.invoke(ifixai_cli, ["findings", "add-regression", report, "B08", "--note", "no admin grants"])
    assert added.exit_code == 0 and "Pinned B08" in added.output
    again = runner.invoke(ifixai_cli, ["findings", "add-regression", report, "B08"])
    assert "already pinned" in again.output
    assert runner.invoke(ifixai_cli, ["findings", "add-regression", report, "B12"]).exit_code == 1
    assert runner.invoke(ifixai_cli, ["findings", "add-regression", report, "Z99"]).exit_code == 1
    assert regressions.load(regressions.DEFAULT_PATH)[0]["note"] == "no admin grants"

    sarif = runner.invoke(ifixai_cli, ["findings", "sarif", report, "-o", "f.sarif", "--target", URL])
    assert sarif.exit_code == 0 and "(1 results)" in sarif.output
    assert json.loads(Path("f.sarif").read_text(encoding="utf-8"))["version"] == "2.1.0"

    printed = runner.invoke(ifixai_cli, ["findings", "comment", report])
    assert printed.output.startswith("<!-- ifixai-self-check -->")
    runner.invoke(ifixai_cli, ["findings", "comment", report, "-o", "c.md"])
    assert Path("c.md").read_text(encoding="utf-8") == printed.output

    (tmp_path / "junk.json").write_text("[]", encoding="utf-8")
    assert runner.invoke(ifixai_cli, ["findings", "explain", str(tmp_path / "junk.json"), "B08"]).exit_code == 1


def test_invoke_run_maps_system_exit(monkeypatch) -> None:
    import click

    @click.command()
    @click.option("--code", default=0)
    def fake(code: int) -> None:
        raise SystemExit(code)

    import ifixai.cli.run as run_mod

    monkeypatch.setattr(run_mod, "run", fake)
    ctx = click.Context(fake)
    assert audit_mod.invoke_run(ctx, code=2) == 2
    assert audit_mod.invoke_run(ctx, code=0) == 0


def test_unconfirmed_target_is_refused_without_a_terminal(runner, fake_run) -> None:
    calls, _ = fake_run
    result = runner.invoke(ifixai_cli, ["audit", URL])
    assert result.exit_code == 1 and "Refusing to probe" in result.output
    assert not calls


def test_unconfirmed_target_asks_on_a_terminal(runner, fake_run, monkeypatch) -> None:
    calls, _ = fake_run
    monkeypatch.setattr(audit_mod, "stdin_is_tty", lambda: True)
    declined = runner.invoke(ifixai_cli, ["audit", URL], input="n\n")
    assert declined.exit_code == 0 and "Not running" in declined.output and not calls
    accepted = runner.invoke(ifixai_cli, ["audit", URL], input="y\n")
    assert accepted.exit_code == 1 and len(calls) == 1


def test_invoke_run_drops_parameters_run_lacks(monkeypatch) -> None:
    import click

    seen: dict = {}

    @click.command()
    @click.option("--code", default=0)
    def fake(code: int) -> None:
        seen["code"] = code

    import ifixai.cli.run as run_mod

    monkeypatch.setattr(run_mod, "run", fake)
    assert audit_mod.invoke_run(click.Context(fake), code=3, target_is_sandboxed=True) == 0
    assert seen == {"code": 3}
