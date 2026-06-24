

import os
from pathlib import Path

import click

SMOKE_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "smoke_tiny.yaml"


PROVIDER_ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "bedrock": "AWS_ACCESS_KEY_ID",
    "huggingface": "HF_TOKEN",
}


def detect_available_providers() -> list[tuple[str, str]]:
    return [
        (provider, env_var)
        for provider, env_var in PROVIDER_ENV_KEYS.items()
        if os.environ.get(env_var)
    ]


@click.command()
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt and print the next command silently.",
)
def init(non_interactive: bool) -> None:
    click.echo(click.style("ifixai init", bold=True))
    click.echo()

    if not SMOKE_FIXTURE_PATH.exists():
        click.echo(
            click.style(
                f"  Error: smoke fixture missing at {SMOKE_FIXTURE_PATH}",
                fg="red",
            )
        )
        raise SystemExit(1)
    click.echo(f"  Smoke fixture: {SMOKE_FIXTURE_PATH}")

    available = detect_available_providers()
    if not available:
        click.echo(click.style("  No provider API keys detected in environment.", fg="yellow"))
        click.echo("  Set one of the following before running tests:")
        for provider, env_var in PROVIDER_ENV_KEYS.items():
            click.echo(f"    - {env_var}  (for --provider {provider})")
        return

    click.echo("  Detected provider keys:")
    for provider, env_var in available:
        click.echo(f"    - {provider} ({env_var} is set)")

    chosen_provider, _ = available[0]
    suggested_command = (
        f"ifixai run "
        f"--provider {chosen_provider} "
        f"--mode standard"
    )

    click.echo()
    click.echo(click.style("Recommended: guided setup (writes ifixai.yaml):", bold=True))
    click.echo("  ifixai setup")
    click.echo()
    click.echo(click.style("Or run directly:", bold=True))
    click.echo(f"  {suggested_command}")
    click.echo()
    click.echo(
        "Mode defaults to 'standard' (CI-friendly, no fixture or judge credentials "
        "required, judge defaults to the system under test). Pass --mode full only "
        "for reference-grade runs with a hand-built fixture and a "
        "multi-judge ensemble."
    )

    if not non_interactive:
        click.confirm("Setup looks correct?", default=True, abort=False)
