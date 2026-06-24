"""``ifixai setup`` — guided wizard that writes ifixai.yaml and can run it.

Uses only click's built-in prompts (no extra TUI dependencies). It never asks
for or stores the system-under-test API key: that stays explicit, supplied to
``ifixai run`` at run time (or prompted for).
"""

from __future__ import annotations

import subprocess
import sys

import click

from ifixai._version import VERSION as IFIXAI_VERSION
from ifixai.cli._branding import print_startup_banner
from ifixai.cli.config_file import CONFIG_FILENAME, JudgeSpec, RunConfig, write_config
from ifixai.cli.init import PROVIDER_ENV_KEYS, detect_available_providers
from ifixai.core.fixture_loader import list_fixture_names, load_fixture
from ifixai.harness.suites import suite_catalog

_PROVIDER_DESCRIPTIONS: dict[str, str] = {
    "openrouter": "One key, many models (OpenAI, Anthropic, Google, Llama, ...)",
    "openai": "OpenAI API (GPT-4o / o-series)",
    "anthropic": "Anthropic API (Claude family)",
    "gemini": "Google Gemini",
    "azure": "Azure OpenAI deployment",
    "bedrock": "AWS Bedrock-hosted models",
    "huggingface": "Hugging Face Inference endpoints",
    "http": "Any OpenAI-compatible HTTP endpoint",
    "langchain": "A LangChain-wrapped model",
    "mock": "Built-in offline mock (no key, just to try the tool)",
}

_ALL_PROVIDERS = [
    "openrouter",
    "openai",
    "anthropic",
    "gemini",
    "azure",
    "bedrock",
    "huggingface",
    "http",
    "langchain",
    "mock",
]


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
    value = click.prompt(
        f"Model id for the {role} (blank = provider default)",
        default="",
        show_default=False,
    ).strip()
    return value or None


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
    click.echo(click.style("Guided setup: a few prompts, then run with no flags.", bold=True))

    available = [p for p, _ in detect_available_providers()]
    if available:
        click.echo(click.style(f"✓ Keys detected for: {', '.join(available)}", fg="green"))
    else:
        click.echo(click.style("No provider keys detected in your environment.", fg="yellow"))

    provider_choices = available + [p for p in _ALL_PROVIDERS if p not in available]
    provider_desc = {
        p: _PROVIDER_DESCRIPTIONS.get(p, "") + (" [key detected]" if p in available else "")
        for p in provider_choices
    }
    provider = _select(
        "Which provider hosts the system under test (the model being graded)?",
        provider_choices,
        default=available[0] if available else "openrouter",
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

    model = _pick_model(provider, role="system under test")

    endpoint = None
    if provider == "http":
        endpoint = click.prompt("HTTP endpoint URL").strip() or None

    click.echo()
    click.echo(
        click.style(
            "Judges grade the answers. A judge from a DIFFERENT vendor gives a citable "
            "score; none = self-judge (advisory, redacted). Judge keys are read from "
            "your environment at run time.",
            dim=True,
        )
    )
    judge_candidates = sorted(set(available) | {provider})
    judges: list[JudgeSpec] = []
    while click.confirm(
        f"Add {'an' if not judges else 'another'} independent judge?",
        default=not judges,
    ):
        jp = _select(
            f"Judge #{len(judges) + 1} provider:",
            judge_candidates,
            default=next((p for p in judge_candidates if p != provider), provider),
            descriptions={p: _PROVIDER_DESCRIPTIONS.get(p, "") for p in judge_candidates},
        )
        jm = _pick_model(jp, role=f"judge #{len(judges) + 1}")
        judges.append(JudgeSpec(provider=jp, model=jm))
        click.echo(click.style(f"  ✓ Judge #{len(judges)}: {jp} / {jm or 'default'}", fg="green"))

    fixtures = list_fixture_names()
    fixture_desc = {}
    for name in fixtures:
        try:
            fx = load_fixture(name)
            fixture_desc[name] = fx.metadata.domain or fx.metadata.name or name
        except Exception:
            fixture_desc[name] = name
    fixture = _select(
        "Fixture (the deployment profile to test against):",
        fixtures,
        default="default" if "default" in fixtures else fixtures[0],
        descriptions=fixture_desc,
    )

    suite_rows = suite_catalog()
    suite_names = [str(r["name"]) for r in suite_rows]
    suite_desc = {str(r["name"]): f"{r['count']} inspections, {r['description']}" for r in suite_rows}
    suite = _select(
        "Suite (which inspections to run):",
        suite_names,
        default="core" if "core" in suite_names else suite_names[0],
        descriptions=suite_desc,
    )

    mode = _select(
        "Run mode:",
        ["standard", "full"],
        default="standard",
        descriptions={
            "standard": "CI-friendly; one judge auto-paired from your env.",
            "full": "Reference-grade; needs a hand-built fixture and >=2 judges.",
        },
    )
    if mode == "full" and len(judges) < 2:
        click.echo(
            click.style(
                "  Note: --mode full needs >=2 judges; add a second or it is rejected at run time.",
                fg="yellow",
            )
        )

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

    click.echo()
    click.echo(click.style("Your configuration:", bold=True))
    click.echo(config.to_yaml())

    if not click.confirm(f"Save to {CONFIG_FILENAME}?", default=True):
        click.echo("Aborted, nothing written.")
        return
    path = write_config(config)
    click.echo(click.style(f"✓ Saved {path}", fg="green"))
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
