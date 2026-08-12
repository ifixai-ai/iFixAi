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
from ifixai.providers.minimax import DEFAULT_BASE_URL, REGIONAL_ENDPOINTS

_PROVIDER_DESCRIPTIONS: dict[str, str] = {
    "openrouter": "One key → many models (OpenAI, Anthropic, Google, Llama…)",
    "openai": "OpenAI API — GPT-4o / o-series",
    "anthropic": "Anthropic API — Claude family",
    "gemini": "Google Gemini",
    "azure": "Azure OpenAI deployment",
    "bedrock": "AWS Bedrock-hosted models",
    "huggingface": "Hugging Face Inference endpoints",
    "minimax": "MiniMax global and China text APIs",
    "http": "Your real deployed agent's OpenAI-compatible HTTP endpoint (recommended)",
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
    "minimax",
    "http",
    "langchain",
    "mock",
]

_CUSTOM_MODEL = "✏  Enter a custom model id…"

_MINIMAX_ENDPOINTS: list[tuple[str, str]] = [
    (
        REGIONAL_ENDPOINTS["global_en"]["openai_base_url"],
        "Global chat completions API (recommended — honours seed and JSON mode)",
    ),
    (
        REGIONAL_ENDPOINTS["cn_zh"]["openai_base_url"],
        "China chat completions API",
    ),
    (
        REGIONAL_ENDPOINTS["global_en"]["anthropic_base_url"],
        "Global messages API (no seed, no JSON mode)",
    ),
    (
        REGIONAL_ENDPOINTS["cn_zh"]["anthropic_base_url"],
        "China messages API (no seed, no JSON mode)",
    ),
]


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
            click.style(
                "No provider API keys detected in your environment.", fg="yellow"
            )
        )

    # Surface the real-agent (http) path first: it's the highest-fidelity SUT.
    # Then providers whose key is already present, then the rest.
    rest = [p for p in _ALL_PROVIDERS if p != "http" and p not in available_names]
    provider_choices = ["http", *available_names, *rest]
    provider_desc = {
        p: _PROVIDER_DESCRIPTIONS.get(p, "")
        + (" — key detected" if p in available_names else "")
        for p in provider_choices
    }
    provider = ui.select(
        "What is the system under test? (Pick 'http' to test your real deployed "
        "agent; any other provider replicates the bare model beneath it.)",
        provider_choices,
        default="http",
        descriptions=provider_desc,
    )
    api_key_env = PROVIDER_ENV_KEYS.get(provider)

    model = _pick_model(provider, role="system under test")

    # Real-agent (http) and azure need an endpoint. For http, observe the deployed
    # agent under its own prompt/governance (grounding=sut, governance runtime).
    endpoint: str | None = None
    grounding: str | None = None
    if provider in ("http", "azure"):
        default_ep = (
            os.environ.get("IFIXAI_HTTP_ENDPOINT") if provider == "http" else None
        )
        endpoint = (
            ui.text(
                "Endpoint URL for the agent under test:", default=default_ep or ""
            ).strip()
            or None
        )
    elif provider == "minimax":
        endpoint = ui.select(
            "MiniMax endpoint:",
            [value for value, _ in _MINIMAX_ENDPOINTS],
            default=DEFAULT_BASE_URL,
            descriptions=dict(_MINIMAX_ENDPOINTS),
        )
    if provider == "http":
        grounding = "sut"

    # Governance: prefer a real declared policy over synthesizing one in the fixture.
    # Skip for http (the live agent enforces its own governance at runtime).
    governance: str | None = None
    if provider != "http":
        gov_path = ui.text(
            "Path to a real governance policy YAML "
            "(blank to skip / synthesize in the fixture):",
            default="",
        ).strip()
        governance = gov_path or None

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
                judge_desc[p] = (
                    f"{base} — same vendor as the SUT; not an independent (citable) judge"
                )
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
                click.style(
                    f"  Ensemble of {len(judges)} judges configured.", fg="cyan"
                )
            )

    fixtures = list_fixture_names()
    fixture_desc = {}
    for name in fixtures:
        try:
            fx = load_fixture(name)
            fixture_desc[name] = fx.metadata.domain or fx.metadata.name or name
        except (
            Exception
        ):  # noqa: BLE001 — best-effort environment probe; any failure falls back to the default
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
        endpoint=endpoint,
        grounding=grounding,
        governance=governance,
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
            "`ifixai run` needs no flags next time for SDK providers whose key is in an "
            "env var. It records only each key's env-var name (never the secret itself), "
            "and the file is git-ignored by default. The http real-agent path still needs "
            "its endpoint token each run (via --api-key or the run prompt), plus "
            "--auth-method for a non-bearer scheme and IFIXAI_EXTRA_HEADERS for custom "
            "headers, which the wizard does not save. Choose No to skip saving and "
            "configure runs with flags instead.",
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
