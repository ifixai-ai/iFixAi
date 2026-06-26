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
    "standard": (
        "One judge grades each answer (auto-paired from a different vendor when you "
        "have a second key). Fast and citable. Best for most runs."
    ),
    "full": (
        "An ensemble of 2+ judges vote on each answer (majority vote, conservative "
        "tie-break), so no single judge decides the grade. Same inspections, sturdier "
        "result. Needs 2+ judge providers."
    ),
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


def _missing_keys(selected: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """For each (role, provider) chosen, return (role, provider, env_var) whose key
    is not set in the environment. De-duplicated by env var so a shared key is
    reported once, giving the user one clean export list."""
    missing: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for role, prov in selected:
        env = PROVIDER_ENV_KEYS.get(prov)
        if env and env not in seen and not os.environ.get(env):
            seen.add(env)
            missing.append((role, prov, env))
    return missing


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

    model = _pick_model(provider, role="system under test")

    judges: list[JudgeSpec] = []
    if provider == "mock":
        # Mock is a free offline preview, so there are no real providers or keys to
        # choose — just ask how many mock judges. A mock judge gives a non-self-judged
        # scorecard offline; 0 = self-judge.
        click.echo()
        choice = ui.select(
            "Mock judges (a judge gives a non-self-judged scorecard, all offline):",
            ["0", "1", "2"],
            default="1",
            descriptions={
                "0": "self-judge: mock grades itself (biased, flagged not citable)",
                "1": "one mock judge: a non-self single-judge run",
                "2": "two mock judges: an ensemble",
            },
        )
        judges = [JudgeSpec(provider="mock", model=None) for _ in range(int(choice))]
        if judges:
            click.echo(
                click.style(f"  ✓ {len(judges)} mock judge(s) configured.", fg="green")
            )
        else:
            click.echo(
                click.style("  Self-judge (advisory, redacted score).", fg="yellow")
            )
    else:
        # Offer every provider as a judge — not just ones with a key already set.
        # The end-of-setup scan reminds the user which keys to export before running.
        judge_candidates = [p for p in provider_choices if p != "mock"]
        judge_desc = {}
        for p in judge_candidates:
            base = _PROVIDER_DESCRIPTIONS.get(p, "")
            env = PROVIDER_ENV_KEYS.get(p)
            if p == provider:
                judge_desc[p] = f"{base} — same vendor as the SUT; not an independent (citable) judge"
            elif p in available_names:
                judge_desc[p] = f"{base} — key detected"
            elif env:
                judge_desc[p] = f"{base} — set {env} before running"
            else:
                judge_desc[p] = base

        click.echo()
        click.echo(
            click.style(
                "Judges score your model's answers. None = self-judge (biased, redacted). "
                "One judge from a DIFFERENT vendor = a citable score; a same-vendor judge is "
                "an independence-limited smoke test, not citable. Two or more = a cross-vendor "
                "ensemble. You can mix providers, or use different models on one key.",
                dim=True,
            )
        )

        # Default the judge to a DIFFERENT vendor — citability requires cross-vendor
        # grading. Prefer one whose key is already present, else any non-SUT provider,
        # and only fall back to the SUT as a last resort.
        judge_default = next(
            (p for p in judge_candidates if p != provider and p in available_names),
            next((p for p in judge_candidates if p != provider), provider),
        )
        add_judge = ui.confirm(
            "Add an independent judge? (recommended — needed for a real score)",
            default=True,
        )
        while add_judge:
            jp = ui.select(
                f"Judge #{len(judges) + 1} — provider:",
                judge_candidates,
                default=judge_default,
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

    click.echo()
    click.echo(
        click.style(
            "Run mode sets how many judges grade each answer, not how many "
            "inspections run. Standard uses one judge; Full uses an ensemble of 2+ "
            "that vote, so no single judge decides your grade.",
            dim=True,
        )
    )
    mode = ui.select(
        "Run mode:",
        ["standard", "full"],
        default="standard",
        descriptions=_MODE_DESCRIPTIONS,
    )

    # eval_mode follows the judge panel. Keep `mode` consistent with it so the
    # wizard never saves a contradictory config (Full mode + self-judge) that the
    # engine rejects at run time.
    if len(judges) >= 2:
        eval_mode = "full"
    elif len(judges) == 1:
        eval_mode = "single"
    else:
        eval_mode = "self"
    if mode == "full" and eval_mode != "full":
        click.echo(
            click.style(
                f"  Full mode needs an ensemble of 2+ judges; you added {len(judges)}. "
                "Saving as Standard mode instead; add a second judge to use Full.",
                fg="yellow",
            )
        )
        mode = "standard"

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

    click.echo(
        click.style(
            f"Saving writes these settings to {CONFIG_FILENAME} in this folder so "
            "`ifixai run` needs no flags next time. It records only each key's env-var "
            "name (never the secret itself), and the file is git-ignored by default. "
            "Choose No to skip saving and configure runs with flags instead.",
            dim=True,
        )
    )
    if not ui.confirm(f"Save to {CONFIG_FILENAME}?", default=True):
        click.echo("Aborted — nothing written.")
        return

    path = write_config(config)
    click.echo(click.style(f"✓ Saved {path}", fg="green"))

    # Scan every provider the user selected (SUT + judges) and report which keys
    # still need exporting before a real run.
    selected = [
        ("system under test", provider),
        *((f"judge #{i}", j.provider) for i, j in enumerate(judges, 1)),
    ]
    missing = _missing_keys(selected)

    click.echo()
    if missing:
        click.echo(
            click.style(
                "Before you run the diagnostic, export these provider keys "
                "(selected, but not in your environment):",
                fg="yellow",
                bold=True,
            )
        )
        for role, prov, env in missing:
            click.echo(click.style(f"  export {env}=…   ({role}: {prov})", fg="yellow"))
    else:
        click.echo(
            click.style(
                "✓ Every selected provider has a key in your environment.", fg="green"
            )
        )
    click.echo()

    if ui.confirm("Run iFixAi now?", default=not missing):
        cmd = [sys.argv[0], "run"]
        if provider == "mock":
            cmd += ["-k", "unused"]
        if missing:
            click.echo(
                click.style(
                    "  Note: the run stops at preflight until the keys above are set "
                    "(the system-under-test key can also be entered when prompted).",
                    fg="yellow",
                )
            )
        click.echo()
        subprocess.run(cmd)
    else:
        click.echo(click.style("When you're ready:", bold=True))
        click.echo(click.style("  ifixai run", fg="cyan"))
