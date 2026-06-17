"""``ifixai setup`` — interactive wizard that writes ifixai.yaml and can run it."""

from __future__ import annotations

import os
import subprocess
import sys

import click

from ifixai._version import VERSION as IFIXAI_VERSION
from ifixai.cli import ui
from ifixai.cli._branding import print_startup_banner
from ifixai.cli.config_file import CONFIG_FILENAME, JudgeSpec, RunConfig, write_config
from ifixai.cli.init import PROVIDER_ENV_KEYS, detect_available_providers
from ifixai.cli.model_catalog import default_model, suggestions
from ifixai.core.fixture_loader import list_fixture_names, load_fixture
from ifixai.harness.suites import suite_catalog

_PROVIDER_DESCRIPTIONS: dict[str, str] = {
    "openrouter": "One key → many models (OpenAI, Anthropic, Google, Llama…)",
    "openai": "OpenAI API — GPT-4o / o-series",
    "anthropic": "Anthropic API — Claude family",
    "gemini": "Google Gemini",
    "azure": "Azure OpenAI deployment",
    "bedrock": "AWS Bedrock-hosted models",
    "huggingface": "Hugging Face Inference endpoints",
    "http": "Any OpenAI-compatible HTTP endpoint",
    "langchain": "A LangChain-wrapped model",
    "mock": "Built-in offline mock — no key, just to try the tool",
}

_MODE_DESCRIPTIONS: dict[str, str] = {
    "standard": "CI-friendly. Works with the default fixture and one provider.",
    "full": "Reference-grade. Needs a hand-built fixture + ≥2 judge providers.",
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

_CUSTOM_MODEL = "✏  Enter a custom model id…"


def _pick_model(provider: str, *, role: str) -> str | None:
    """Model picker (default / suggestions / custom); None means provider default."""
    dm = default_model(provider)
    default_label = f"Provider default ({dm})" if dm else "Provider default"
    sugg = suggestions(provider)

    labels = [default_label, *[m for m, _ in sugg], _CUSTOM_MODEL]
    desc = {default_label: "Use the provider's built-in default model"}
    for model_id, description in sugg:
        desc[model_id] = description
    desc[_CUSTOM_MODEL] = "Type an exact model id yourself"

    choice = ui.select(
        f"Which model should be the {role}?",
        labels,
        default=default_label,
        descriptions=desc,
    )
    if choice == _CUSTOM_MODEL:
        return ui.text("Model id:", default=dm or "").strip() or None
    if choice == default_label:
        return None
    return choice


@click.command()
@click.pass_context
def setup(ctx: click.Context) -> None:
    """Interactively configure a run and save it to ifixai.yaml."""
    if not ui.is_interactive():
        click.echo(
            click.style(
                "Error: `ifixai setup` needs an interactive terminal.\n"
                "In a script or CI, configure the run with explicit flags, e.g.:\n"
                "  ifixai run --provider openai --suite core --mode standard",
                fg="red",
            ),
            err=True,
        )
        raise SystemExit(1)

    print_startup_banner(IFIXAI_VERSION)
    click.echo(
        click.style("Guided setup — a few prompts, then run zero-flag.", bold=True)
    )
    click.echo()

    available = detect_available_providers()
    available_names = [p for p, _ in available]
    if available_names:
        click.echo(
            click.style(
                f"✓ Detected credentials for: {', '.join(available_names)}", fg="green"
            )
        )
    else:
        click.echo(
            click.style("No provider API keys detected in your environment.", fg="yellow")
        )

    provider_choices = available_names + [
        p for p in _ALL_PROVIDERS if p not in available_names
    ]
    provider_desc = {
        p: _PROVIDER_DESCRIPTIONS.get(p, "")
        + (" — key detected" if p in available_names else "")
        for p in provider_choices
    }
    provider = ui.select(
        "Which provider hosts the system under test?",
        provider_choices,
        default=available_names[0] if available_names else "openrouter",
        descriptions=provider_desc,
    )
    api_key_env = PROVIDER_ENV_KEYS.get(provider)
    if api_key_env and provider not in available_names:
        click.echo(
            click.style(
                f"  Note: export {api_key_env} before running (no key detected now).",
                fg="yellow",
            )
        )

    model = _pick_model(provider, role="system under test")

    judge_candidates = sorted(set(available_names) | {provider})
    judge_desc = {}
    for p in judge_candidates:
        base = _PROVIDER_DESCRIPTIONS.get(p, "")
        if p == provider:
            judge_desc[p] = f"{base} — reuses this key; pick a different model"
        else:
            judge_desc[p] = f"{base} — key detected"

    click.echo()
    click.echo(
        click.style(
            "Judges score your model's answers. None = self-judge (biased, score "
            "redacted). One = real score. Two or more = ensemble (averaged). "
            "You can add several judges — even different models on the same key.",
            dim=True,
        )
    )

    judges: list[JudgeSpec] = []
    add_judge = ui.confirm(
        "Add an independent judge? (recommended — needed for a real score)",
        default=True,
    )
    while add_judge:
        jp = ui.select(
            f"Judge #{len(judges) + 1} — provider:",
            judge_candidates,
            default=provider,
            descriptions=judge_desc,
        )
        jm = _pick_model(jp, role=f"judge #{len(judges) + 1}")
        judges.append(JudgeSpec(provider=jp, model=jm))
        click.echo(
            click.style(
                f"  ✓ Judge #{len(judges)}: {jp} / {jm or 'provider default'}",
                fg="green",
            )
        )
        add_judge = ui.confirm(
            "Add another judge? (2+ judges = ensemble)", default=False
        )

    if not judges:
        click.echo(
            click.style(
                "  No judge selected — running self-judge (advisory, redacted score).",
                fg="yellow",
            )
        )
    elif len(judges) >= 2:
        click.echo(
            click.style(f"  Ensemble of {len(judges)} judges configured.", fg="cyan")
        )

    fixtures = list_fixture_names()
    fixture_desc = {}
    for name in fixtures:
        try:
            fx = load_fixture(name)
            fixture_desc[name] = fx.metadata.domain or fx.metadata.name or name
        except Exception:
            fixture_desc[name] = name
    fixture = ui.select(
        "Fixture (the deployment profile to test against):",
        fixtures,
        default="default" if "default" in fixtures else fixtures[0],
        descriptions=fixture_desc,
    )

    suite_rows = suite_catalog()
    suite_names = [r["name"] for r in suite_rows]
    suite_desc = {
        r["name"]: f"{r['count']} inspections — {r['description']}" for r in suite_rows
    }
    suite = ui.select(
        "Suite (which inspections to run):",
        suite_names,
        default="core",
        descriptions=suite_desc,
    )

    mode = ui.select(
        "Run mode:",
        ["standard", "full"],
        default="standard",
        descriptions=_MODE_DESCRIPTIONS,
    )

    if mode == "full" or len(judges) >= 2:
        eval_mode = "full" if len(judges) >= 2 else "self"
    elif len(judges) == 1:
        eval_mode = "single"
    else:
        eval_mode = "self"
    if mode == "full" and len(judges) < 2:
        click.echo(
            click.style(
                "  Note: --mode full needs ≥2 judges and a hand-built fixture; "
                "add a second judge or this will be rejected at run time.",
                fg="yellow",
            )
        )

    config = RunConfig(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        fixture=fixture,
        suite=suite,
        mode=mode,
        eval_mode=eval_mode,
        judges=judges,
    )

    click.echo()
    click.echo(click.style("Your configuration:", bold=True))
    click.echo(config.to_yaml())

    if not ui.confirm(f"Save to {CONFIG_FILENAME}?", default=True):
        click.echo("Aborted — nothing written.")
        return

    path = write_config(config)
    click.echo(click.style(f"✓ Saved {path}", fg="green"))
    click.echo()

    if ui.confirm("Run iFixAi now?", default=True):
        cmd = [sys.argv[0], "run"]
        if provider == "mock":
            cmd += ["-k", "unused"]
        elif api_key_env and not os.environ.get(api_key_env):
            click.echo(
                click.style(
                    f"  {api_key_env} is not set — `run` will prompt for the key.",
                    fg="yellow",
                )
            )
        click.echo()
        subprocess.run(cmd)
    else:
        click.echo(click.style("When you're ready:", bold=True))
        click.echo(click.style("  ifixai run", fg="cyan"))
        if api_key_env:
            click.echo(
                click.style(f"  (export {api_key_env} first)", dim=True)
            )
