"""`ifixai audit <agent-url>`: a self-check in one command, and `ifixai findings`.

`audit` is `ifixai run` with the choices made for you: the agent is an
OpenAI-shaped chat endpoint at the URL, the suite is the fast `smoke` set plus
every inspection pinned in `.ifixai/regressions.yaml`, and the result is
labelled a self-check: never certified, never a badge.

The judge: with `--judge-provider` it is that provider; otherwise any provider
key already in the environment is paired the usual way (cross-provider, never
the agent judging itself); with none, only structural checks run and most
inspections come back undecided, which the verdict says plainly. A judge runs
on the caller's own key; this command never supplies one.

Exit codes: 0 nothing failed, 1 a check or a pinned regression failed,
2 the self-check could not be evaluated (bad input, a crash, or nothing could
be decided).

`findings` works on a report a run already wrote: explain a failure, pin it
as a regression, convert it to SARIF, render the PR comment.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import click

from ifixai.harness.suites import SUITE_NAMES, resolve_suite
from ifixai.providers.resolver import detect_available_credentials
from ifixai.selfcheck import comment as comment_mod
from ifixai.selfcheck import regressions as regressions_mod
from ifixai.selfcheck.explain import explain
from ifixai.selfcheck.result import (
    NOTICE,
    SelfCheckError,
    build_result,
    load_scorecard,
)
from ifixai.selfcheck.sarif import choose_anchor, to_sarif

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNAVAILABLE = 2
DEFAULT_OUT = Path(".ifixai") / "self-check"
RESULT_NAME = "self-check.json"
AGENT_KEY_ENV = "IFIXAI_AGENT_KEY"
# Judge choices `audit` offers: the real providers plus `mock`, which makes no
# calls and exists so the command can be exercised end to end for free.
JUDGE_CHOICES = ("anthropic", "openai", "gemini", "openrouter", "azure", "bedrock", "mock")
FAIL_ON = ("any", "regressions", "never")


def check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise click.BadParameter("must be an http(s) URL such as https://agent.example.com/v1", param_hint="AGENT_URL")
    if parts.username or parts.password:
        raise click.BadParameter(
            "must not carry credentials; pass the agent's key with --agent-key or "
            f"{AGENT_KEY_ENV}",
            param_hint="AGENT_URL",
        )
    return url


def inspection_ids(suite: str, pinned: list[str], extra: tuple[str, ...]) -> list[str]:
    resolution = resolve_suite(suite)
    if resolution["unknown"]:
        raise click.BadParameter(
            f"unknown suite {suite!r}; choose one of {', '.join(SUITE_NAMES)}", param_hint="--suite"
        )
    return list(dict.fromkeys([*resolution["test_ids"], *(e.upper() for e in extra), *pinned]))


def judge_plan(judge_provider: str | None) -> tuple[str | None, tuple[str, ...], str]:
    """(eval_mode, judge providers, a line saying what will judge)."""
    if judge_provider:
        return "single", (judge_provider,), f"Judge: {judge_provider}"
    if detect_available_credentials(os.environ):
        return None, (), "Judge: paired from the provider keys in your environment (your key, your cost)"
    return (
        "deterministic",
        (),
        (
            "Judge: none. No provider key found, so only structural checks run and most "
            "inspections will be undecided. Set OPENAI_API_KEY or ANTHROPIC_API_KEY, or pass "
            "--judge-provider, for a verdict on the rest."
        ),
    )


def newest_report(out: Path, started: float) -> Path | None:
    """The report this run wrote: the newest JSON in `out` written since it started."""
    candidates = [
        p for p in out.glob("*.json")
        if p.name != RESULT_NAME and p.is_file() and p.stat().st_mtime >= started
    ]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


SANDBOX_QUESTION = (
    "Is this a test deployment with sandboxed backends and no real data? "
    "The self-check sends it adversarial prompts [y/N]"
)


def stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def confirm_target(url: str, confirmed: bool) -> None:
    """Ask once whether the URL is safe to probe; refuse when nobody can answer."""
    if confirmed:
        return
    if not stdin_is_tty():
        raise click.ClickException(
            "Refusing to probe an unconfirmed agent without a terminal to ask. Pass --yes "
            "once you have pointed it at a test deployment with sandboxed backends."
        )
    answer = click.prompt(SANDBOX_QUESTION, default="n", show_default=False)
    if answer.strip().lower() not in {"y", "yes"}:
        click.echo("Not running. Point it at a sandboxed test deployment, then re-run.")
        sys.exit(EXIT_OK)


def invoke_run(ctx: click.Context, **params: Any) -> int:
    """Run `ifixai run` in-process; returns its exit code. Patched in tests.

    Parameters `run` does not have (builds differ) are dropped, never guessed at.
    """
    from ifixai.cli.run import run

    accepted = {param.name for param in run.params}
    params = {name: value for name, value in params.items() if name in accepted}
    try:
        ctx.invoke(run, **params)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    return 0


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def exit_code_for(result: dict[str, Any], fail_on: str) -> int:
    counts = result["counts"]
    regressions = result.get("regressions") or {}
    if regressions.get("failed") and fail_on != "never":
        return EXIT_FAILED
    if counts["fail"] and fail_on == "any":
        return EXIT_FAILED
    if counts["total"] == 0 or counts["undecided"] == counts["total"]:
        return EXIT_UNAVAILABLE
    return EXIT_OK


def emit_outputs(
    result: dict[str, Any],
    scorecard: dict[str, Any],
    *,
    out: Path,
    sarif: str | None,
    comment: str | None,
    fixture: str | None,
    as_json: bool,
) -> None:
    write_json(out / RESULT_NAME, result)
    if sarif:
        anchor = choose_anchor(Path.cwd(), fixture)
        write_json(Path(sarif), to_sarif(scorecard, anchor=anchor, target=result["target"], verdict=result["verdict"]))
    if comment:
        Path(comment).parent.mkdir(parents=True, exist_ok=True)
        Path(comment).write_text(comment_mod.render(result), encoding="utf-8")
    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return
    click.echo()
    click.echo(click.style(result["verdict"], bold=True))
    for finding in result["findings"][:10]:
        click.echo(f"  - {finding['inspection']} {finding['name']}")
    regressions = result.get("regressions") or {}
    if regressions.get("failed"):
        click.echo(click.style("  Pinned regressions failed again: " + ", ".join(regressions["failed"]), fg="red"))
    click.echo(f"Result: {out / RESULT_NAME}")
    if result["findings"]:
        click.echo(f"Explain one: ifixai findings explain {result['report_path']} {result['findings'][0]['inspection']}")
    click.echo(NOTICE)


@click.command()
@click.argument("agent_url")
@click.option("--agent-key", envvar=AGENT_KEY_ENV, default=None,
              help=f"The agent's own API key, if it needs one. Prefer {AGENT_KEY_ENV} over the flag.")
@click.option("--model", default=None, help="Model name to send to the agent, if it routes on one.")
@click.option("--suite", default="smoke", show_default=True,
              help="Which inspections to run. `smoke` is the fast set; see `ifixai list suites`.")
@click.option("--test", "-b", "extra", multiple=True, help="Add an inspection by id (repeatable).")
@click.option("--fixture", "-f", default=None, help="Fixture name or YAML path describing the agent.")
@click.option("--judge-provider", type=click.Choice(JUDGE_CHOICES), default=None,
              help="Judge provider. Default: paired from keys in the environment, else none.")
@click.option("--judge-model", default=None, help="Judge model, with --judge-provider.")
@click.option("--regressions", "regressions_path", type=click.Path(dir_okay=False),
              default=str(regressions_mod.DEFAULT_PATH), show_default=True,
              help="Pinned findings to re-check on every run.")
@click.option("--out", type=click.Path(file_okay=False), default=str(DEFAULT_OUT), show_default=True,
              help="Where the report and self-check.json are written.")
@click.option("--sarif", type=click.Path(dir_okay=False), default=None,
              help="Also write SARIF 2.1.0 here, for GitHub code scanning.")
@click.option("--comment", type=click.Path(dir_okay=False), default=None,
              help="Also write the pull-request comment (markdown) here.")
@click.option("--fail-on", type=click.Choice(FAIL_ON), default="any", show_default=True,
              help="`any` failure, only pinned `regressions`, or `never` (report only).")
@click.option("--yes", "-y", "confirmed", is_flag=True,
              help="Confirm the URL is a test deployment with sandboxed backends and no real data.")
@click.option("--json", "as_json", is_flag=True, help="Print the self-check result as JSON.")
@click.pass_context
def audit(
    ctx: click.Context,
    agent_url: str,
    agent_key: str | None,
    model: str | None,
    suite: str,
    extra: tuple[str, ...],
    fixture: str | None,
    judge_provider: str | None,
    judge_model: str | None,
    regressions_path: str,
    out: str,
    sarif: str | None,
    comment: str | None,
    fail_on: str,
    confirmed: bool,
    as_json: bool,
) -> None:
    """Self-check the agent at AGENT_URL (an OpenAI-shaped chat endpoint).

    Free and local. The result is labelled a self-check: it is not certified
    and carries no badge.
    """
    url = check_url(agent_url)
    confirm_target(url, confirmed)
    try:
        pinned_entries = regressions_mod.load(regressions_path)
    except regressions_mod.RegressionFileError as exc:
        raise click.ClickException(str(exc)) from None
    pinned = regressions_mod.local_inspections(pinned_entries)
    ids = inspection_ids(suite, pinned, extra)
    eval_mode, judges, judge_line = judge_plan(judge_provider)

    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Filesystem mtimes can be coarser than the clock; allow a second of slack.
    started = time.time() - 1.0
    click.echo(f"iFixAi self-check (not certified) of {url}", err=True)
    click.echo(f"Inspections: {len(ids)} ({suite} suite" + (f" + {len(pinned)} pinned" if pinned else "") + ")", err=True)
    click.echo(judge_line, err=True)

    run_code = invoke_run(
        ctx,
        provider="http",
        endpoint=url,
        # The http provider sends a bearer header when a key is set; a keyless
        # agent gets a placeholder rather than an interactive prompt.
        api_key=agent_key or "unused",
        model=model,
        fixture=fixture,
        test=tuple(ids),
        eval_mode=eval_mode,
        judge_provider=judges,
        judge_model=(judge_model,) if (judge_model and judges) else (),
        output=str(out_dir),
        report_format="json",
        # Confirmed above; older builds without the flag simply drop it.
        target_is_sandboxed=True,
        min_score=0.0,
        system_name="self-check",
        quiet=True,
    )
    report = newest_report(out_dir, started)
    if report is None:
        click.echo(
            click.style(
                f"The self-check did not produce a report (run exit {run_code}); nothing was decided.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(EXIT_UNAVAILABLE)
    scorecard = load_scorecard(report)
    result = build_result(
        scorecard,
        target=url,
        suite=suite,
        report_path=str(report),
        regressions=regressions_mod.check(pinned_entries, scorecard) if pinned_entries else None,
    )
    emit_outputs(result, scorecard, out=out_dir, sarif=sarif, comment=comment, fixture=fixture, as_json=as_json)
    sys.exit(exit_code_for(result, fail_on))


def read_report(report: str) -> dict[str, Any]:
    try:
        return load_scorecard(report)
    except SelfCheckError as exc:
        raise click.ClickException(str(exc)) from None


@click.group()
def findings() -> None:
    """Work with the findings in a report `ifixai audit` or `ifixai run` wrote."""


@findings.command("explain")
@click.argument("report", type=click.Path(exists=True, dir_okay=False))
@click.argument("inspection")
@click.option("--json", "as_json", is_flag=True)
def explain_cmd(report: str, inspection: str, as_json: bool) -> None:
    """Explain why INSPECTION failed, in plain language, from the evidence."""
    try:
        document = explain(read_report(report), inspection)
    except KeyError as exc:
        raise click.ClickException(str(exc.args[0])) from None
    click.echo(json.dumps(document, indent=2, ensure_ascii=False) if as_json else document["text"])


@findings.command("add-regression")
@click.argument("report", type=click.Path(exists=True, dir_okay=False))
@click.argument("inspection")
@click.option("--note", default=None, help="One line: what must not happen again.")
@click.option("--file", "path", type=click.Path(dir_okay=False),
              default=str(regressions_mod.DEFAULT_PATH), show_default=True)
def add_regression_cmd(report: str, inspection: str, note: str | None, path: str) -> None:
    """Pin a failed INSPECTION so every later `ifixai audit` re-checks it."""
    try:
        entry = regressions_mod.entry_from_scorecard(read_report(report), inspection, note=note)
        added = regressions_mod.add(path, entry)
    except KeyError as exc:
        raise click.ClickException(str(exc.args[0])) from None
    except (ValueError, regressions_mod.RegressionFileError) as exc:
        raise click.ClickException(str(exc)) from None
    if added:
        click.echo(f"Pinned {entry['inspection']} ({entry['title']}) in {path}. Commit that file.")
    else:
        click.echo(f"{entry['inspection']} is already pinned in {path}.")


@findings.command("sarif")
@click.argument("report", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", "-o", type=click.Path(dir_okay=False), required=True)
@click.option("--target", default=None, help="The agent URL, for stable fingerprints.")
@click.option("--fixture", default=None, help="Fixture file to anchor results on.")
def sarif_cmd(report: str, output: str, target: str | None, fixture: str | None) -> None:
    """Convert a report to SARIF 2.1.0 for GitHub code scanning."""
    scorecard = read_report(report)
    result = build_result(scorecard, target=target, report_path=report)
    anchor = choose_anchor(Path.cwd(), fixture)
    write_json(Path(output), to_sarif(scorecard, anchor=anchor, target=result["target"], verdict=result["verdict"]))
    click.echo(f"SARIF: {output} ({result['counts']['fail']} results)")


@findings.command("comment")
@click.argument("report", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", "-o", type=click.Path(dir_okay=False), default=None)
def comment_cmd(report: str, output: str | None) -> None:
    """Render the pull-request comment for a report."""
    text = comment_mod.render(build_result(read_report(report), report_path=report))
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        click.echo(text, nl=False)


@click.command("mcp")
@click.option("--root", type=click.Path(file_okay=False, exists=True), default=".", show_default=True,
              help="Project folder the tools read reports from and pin regressions in.")
def mcp(root: str) -> None:
    """Serve the self-check tools to an MCP client (Claude Code, Cursor) over stdio."""
    from ifixai.selfcheck.mcp_server import Server

    Server(Path(root)).serve(sys.stdin, sys.stdout)


@findings.command("validate-regressions")
@click.argument("path", type=click.Path(dir_okay=False), default=str(regressions_mod.DEFAULT_PATH))
def validate_regressions_cmd(path: str) -> None:
    """Check that the regressions file is well formed (fast; runs nothing)."""
    try:
        entries = regressions_mod.load(path)
    except regressions_mod.RegressionFileError as exc:
        raise click.ClickException(str(exc)) from None
    local = regressions_mod.local_inspections(entries)
    hosted = sum(1 for e in entries if "hosted" in e)
    click.echo(f"{path}: {len(local)} pinned inspection(s), {hosted} hosted finding(s).")
