"""``ifixai setup`` — guided wizard that writes ifixai.yaml and can run it.

Uses only click's built-in prompts (no extra TUI dependencies). It never asks
for or stores the system-under-test API key: that stays explicit, supplied to
``ifixai run`` at run time (or prompted for).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import click

from ifixai._version import VERSION as IFIXAI_VERSION
from ifixai.cli._branding import print_startup_banner
from ifixai.cli.config_file import (
    JudgeSpec,
    RunConfig,
    config_path,
    write_config,
)
from ifixai.cli.init import PROVIDER_ENV_KEYS, detect_available_providers
from ifixai.core.fixture_loader import list_fixture_names, load_fixture
from ifixai.harness.suites import suite_catalog

_TOTAL_STEPS = 5

_PROVIDER_DESCRIPTIONS: dict[str, str] = {
    "openrouter": "One key, many models (OpenAI, Anthropic, Google, Llama, ...)",
    "openai": "OpenAI API (GPT-4o / o-series)",
    "anthropic": "Anthropic API (Claude family)",
    "gemini": "Google Gemini",
    "azure": "Azure OpenAI deployment",
    "bedrock": "AWS Bedrock-hosted models",
    "huggingface": "Hugging Face Inference endpoints",
    "http": "Any OpenAI-compatible HTTP endpoint (point at your agent)",
    "langchain": "A LangChain-wrapped agent",
    "mock": "Built-in offline mock (no key, just to try the tool)",
}

# Providers that front a bare model API vs transports for a wrapped agent.
_PROVIDERS_DIR = Path(__file__).resolve().parents[1] / "providers"


def _provider_default_model(provider: str) -> str | None:
    """The provider's DEFAULT_MODEL, read from source (no SDK import, no drift)."""
    try:
        text = (_PROVIDERS_DIR / f"{provider}.py").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^DEFAULT_MODEL\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else None


_MODEL_PROVIDERS = [
    "openrouter",
    "openai",
    "anthropic",
    "gemini",
    "azure",
    "bedrock",
    "huggingface",
    "mock",
]
_AGENT_PROVIDERS = ["http", "langchain"]


def _step(n: int, title: str, subtitle: str = "") -> None:
    """Print a numbered step header so the flow reads as discrete stages."""
    click.echo()
    click.echo(click.style(f"── Step {n} of {_TOTAL_STEPS} · {title} ", bold=True) + click.style("─" * 24, dim=True))
    if subtitle:
        click.echo(click.style(subtitle, dim=True))


def _select(
    prompt: str,
    options: list[str],
    *,
    default: str,
    descriptions: dict[str, str] | None = None,
) -> str:
    """Numbered single-choice menu via click prompts (no TUI deps)."""
    click.echo()
    click.echo(click.style(prompt, bold=True))
    descriptions = descriptions or {}
    for i, opt in enumerate(options, 1):
        desc = descriptions.get(opt, "")
        suffix = f"  {click.style('— ' + desc, dim=True)}" if desc else ""
        click.echo(f"  {i}) {opt}{suffix}")
    default_idx = options.index(default) + 1 if default in options else 1
    chosen = click.prompt(
        "Choose a number",
        type=click.IntRange(1, len(options)),
        default=default_idx,
        show_default=True,
    )
    return options[chosen - 1]


def _pick_model(provider: str, *, role: str) -> str | None:
    """Free-text model id; blank means use the provider's default model."""
    default_model = _provider_default_model(provider)
    label = (
        f"(blank = provider default: {default_model})"
        if default_model
        else "(blank = provider default)"
    )
    value = click.prompt(
        f"Model id for the {role} {click.style(label, dim=True)}",
        default="",
        show_default=False,
    ).strip()
    return value or None


def _judge_note(provider: str, available: list[str]) -> None:
    """Tell the user to export a judge key if it isn't already in the env."""
    env = PROVIDER_ENV_KEYS.get(provider)
    if provider != "mock" and env and provider not in available:
        click.echo(
            click.style(
                f"    Note: export {env} before running (judge keys are read from the env).",
                fg="yellow",
            )
        )


def _add_judge(
    idx: int,
    sut_provider: str,
    candidates: list[str],
    available: list[str],
) -> JudgeSpec:
    """Prompt for one judge, steering away from a non-independent same-vendor pick."""
    desc = {p: _PROVIDER_DESCRIPTIONS.get(p, "") for p in candidates}
    default = next((p for p in candidates if p != sut_provider), candidates[0])
    while True:
        jp = _select(f"Judge #{idx} provider:", candidates, default=default, descriptions=desc)
        if jp == sut_provider:
            click.echo(
                click.style(
                    f"  ⚠ {jp} is also the system under test — that is not an independent "
                    "grade, so the score is not citable.",
                    fg="yellow",
                    bold=True,
                )
            )
            if not click.confirm("  Use it anyway?", default=False):
                continue
        jm = _pick_model(jp, role=f"judge #{idx}")
        _judge_note(jp, available)
        click.echo(click.style(f"  ✓ Judge #{idx}: {jp} / {jm or 'provider default'}", fg="green"))
        return JudgeSpec(provider=jp, model=jm)


def _independence(judges: list[JudgeSpec], sut_provider: str) -> str:
    """One-word verdict on whether the configured judges give a citable grade."""
    if not judges:
        return "self-judge (advisory, redacted)"
    if any(j.provider == sut_provider for j in judges):
        return "NOT independent (judge shares the SUT vendor)"
    return "independent ✓"


@click.command()
def setup() -> None:
    """Interactively configure a run and save it to ifixai.yaml."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        click.echo(
            click.style(
                "Error: `ifixai setup` needs an interactive terminal.\n"
                "In a script or CI, use explicit flags, e.g.:\n"
                "  ifixai run --provider openai --suite core --mode standard",
                fg="red",
            ),
            err=True,
        )
        raise SystemExit(1)

    print_startup_banner(IFIXAI_VERSION)
    click.echo(click.style("Guided setup: 5 steps, then run with no flags.", bold=True))

    available = [p for p, _ in detect_available_providers()]
    if available:
        click.echo(click.style(f"✓ Keys detected for: {', '.join(available)}", fg="green"))
    else:
        click.echo(click.style("No provider keys detected in your environment.", fg="yellow"))

    # ── Step 1 · Target ────────────────────────────────────────────────
    _step(1, "Target", "What you are grading: a bare model API, or your wrapped agent.")
    target = _select(
        "What are you testing?",
        ["a bare model API", "an agent (system prompt, tools, retrieval)"],
        default="a bare model API",
    )
    is_agent = target.startswith("an agent")
    if is_agent:
        click.echo(
            click.style(
                "  iFixAi reaches your agent as a black box. Point --provider http at an "
                "OpenAI-compatible endpoint, or use langchain. Exposing capability hooks "
                "(list_tools, get_audit_trail, authorize_tool, retrieve_sources) lets more "
                "inspections score instead of 'insufficient_evidence'. See docs/testing-your-agent.md.",
                dim=True,
            )
        )
        ordered = _AGENT_PROVIDERS + _MODEL_PROVIDERS
        provider_default = "http"
    else:
        ordered = available + [p for p in _MODEL_PROVIDERS if p not in available] + _AGENT_PROVIDERS
        provider_default = available[0] if available else "openrouter"

    provider_desc = {
        p: _PROVIDER_DESCRIPTIONS.get(p, "") + (" [key detected]" if p in available else "")
        for p in ordered
    }
    provider = _select(
        "Which provider hosts it?",
        ordered,
        default=provider_default,
        descriptions=provider_desc,
    )

    key_env = PROVIDER_ENV_KEYS.get(provider)
    if provider != "mock" and key_env and provider not in available:
        click.echo(
            click.style(
                f"  Note: export {key_env} before running, or `run` will prompt for the key.",
                fg="yellow",
            )
        )

    endpoint = None
    if provider == "http":
        endpoint = click.prompt("  Endpoint URL (OpenAI-compatible)").strip() or None

    model = _pick_model(provider, role="system under test")

    # ── Step 2 · Judge ─────────────────────────────────────────────────
    _step(
        2,
        "Judge",
        "Grades the answers. A DIFFERENT vendor makes the score citable; none = "
        "self-judge (advisory, redacted). Judge keys come from your env at run time.",
    )
    judge_candidates = sorted(set(available) | set(_MODEL_PROVIDERS) | {provider})
    judges: list[JudgeSpec] = []
    while click.confirm(
        f"Add {'an' if not judges else 'another'} independent judge?",
        default=not judges,
    ):
        judges.append(_add_judge(len(judges) + 1, provider, judge_candidates, available))

    # ── Step 3 · Fixture ───────────────────────────────────────────────
    _step(3, "Fixture", "The deployment profile (role, tools, governance) to test against.")
    fixtures = list_fixture_names()
    fixture_desc = {}
    for name in fixtures:
        try:
            fx = load_fixture(name)
            fixture_desc[name] = fx.metadata.domain or fx.metadata.name or name
        except Exception:
            fixture_desc[name] = name
    fixture = _select(
        "Fixture:",
        fixtures,
        default="default" if "default" in fixtures else fixtures[0],
        descriptions=fixture_desc,
    )

    # ── Step 4 · Suite ─────────────────────────────────────────────────
    _step(4, "Suite", "Which inspections to run.")
    suite_rows = suite_catalog()
    suite_names = [str(r["name"]) for r in suite_rows]
    suite_desc = {str(r["name"]): f"{r['count']} inspections, {r['description']}" for r in suite_rows}
    suite = _select(
        "Suite:",
        suite_names,
        default="core" if "core" in suite_names else suite_names[0],
        descriptions=suite_desc,
    )

    # ── Step 5 · Mode ──────────────────────────────────────────────────
    _step(5, "Mode", "How rigorous the run is.")
    mode = _select(
        "Run mode:",
        ["standard", "full"],
        default="standard",
        descriptions={
            "standard": "CI-friendly; one judge (auto-paired from your env if none set above).",
            "full": "Reference-grade; needs a hand-built fixture and >=2 judges.",
        },
    )
    # Full mode is unrunnable without >=2 judges — fix it here, not at run time.
    while mode == "full" and len(judges) < 2:
        click.echo(
            click.style(
                f"  Full mode needs >=2 judges; you have {len(judges)}.",
                fg="yellow",
            )
        )
        choice = _select(
            "How do you want to proceed?",
            ["add a judge now", "switch to standard mode"],
            default="add a judge now",
        )
        if choice == "switch to standard mode":
            mode = "standard"
        else:
            judges.append(_add_judge(len(judges) + 1, provider, judge_candidates, available))

    # Leave eval_mode unset unless an ensemble is configured, so `run` picks the
    # best available mode (auto-pairing a judge from the environment) by default.
    eval_mode = "full" if len(judges) >= 2 else None

    config = RunConfig(
        provider=provider,
        model=model,
        endpoint=endpoint,
        fixture=fixture,
        suite=suite,
        mode=mode,
        eval_mode=eval_mode,
        judges=judges,
    )

    # ── Review ─────────────────────────────────────────────────────────
    click.echo()
    click.echo(click.style("Review", bold=True))
    target_kind = "agent" if is_agent else "model"
    click.echo(f"  Target ({target_kind}):  {provider} / {model or '(provider default)'}"
               + (f"  @ {endpoint}" if endpoint else ""))
    if judges:
        for i, j in enumerate(judges, 1):
            click.echo(f"  Judge #{i}:       {j.provider} / {j.model or '(provider default)'}")
    else:
        click.echo("  Judge:          none")
    click.echo(f"  Grade:          {_independence(judges, provider)}")
    suite_count = next((r["count"] for r in suite_rows if str(r["name"]) == suite), "?")
    click.echo(f"  Fixture:        {fixture}")
    click.echo(f"  Suite:          {suite} ({suite_count} inspections)")
    click.echo(f"  Mode:           {mode}")
    click.echo(click.style("  Will run:       ifixai run   (SUT key entered at run time, never saved)", dim=True))

    click.echo()
    # Saving is the whole point of the wizard, and it must never block the run,
    # so always write and continue (no save prompt).
    existed = config_path().exists()
    path = write_config(config)
    click.echo(click.style(f"✓ {'Updated' if existed else 'Saved'} {path}", fg="green"))
    click.echo()

    if click.confirm("Run iFixAi now?", default=True):
        cmd = [sys.executable, "-m", "ifixai.cli.main", "run"]
        if provider == "mock":
            cmd += ["-k", "unused"]
        click.echo()
        subprocess.run(cmd)
    else:
        click.echo(click.style("When you're ready, just run:  ", bold=True) + click.style("ifixai run", fg="cyan"))
        if provider != "mock" and key_env:
            click.echo(click.style(f"  (export {key_env} first, or it will prompt for the key)", dim=True))
