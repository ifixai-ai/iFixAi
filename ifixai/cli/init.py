

import os
from pathlib import Path

import click

from ifixai.providers.resolver import credential_env_vars, credential_requires_all

SMOKE_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "smoke_tiny.yaml"


PROVIDER_ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "orcarouter": "ORCAROUTER_API_KEY",
    "requesty": "REQUESTY_API_KEY",
    "vercel": "AI_GATEWAY_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "bedrock": "AWS_ACCESS_KEY_ID",
    "huggingface": "HF_TOKEN",
    # Last on purpose: this variable is usually set for wrangler or Terraform,
    # so it must not outrank a model vendor's key as the suggested provider.
    "cloudflare": "CLOUDFLARE_API_TOKEN",
}

# Settings a provider cannot make a call without that are not its secret.
# Listed beside the key by `ifixai init` and the setup wizard.
PROVIDER_COMPANION_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "cloudflare": ("CLOUDFLARE_ACCOUNT_ID",),
}


def detect_available_providers() -> list[tuple[str, str]]:
    """Providers whose key and every companion setting are in the environment.

    A key without its companion cannot make a call, so it is not offered as ready.
    """
    available = []
    for provider, primary in PROVIDER_ENV_KEYS.items():
        # Required credential pairs are not interchangeable aliases.
        candidates = (primary,) if credential_requires_all(provider) else credential_env_vars(provider) or (primary,)
        env_var = next((name for name in candidates if os.environ.get(name)), None)
        if env_var and all(
            os.environ.get(companion)
            for companion in PROVIDER_COMPANION_ENV_KEYS.get(provider, ())
        ):
            available.append((provider, env_var))
    return available


def load_dotenv_file(path: "Path | None" = None) -> list[str]:
    """Load ``KEY=VALUE`` pairs from a ``.env`` file (the cwd's by default) into
    ``os.environ`` without overriding variables already set, returning the names of
    the keys it added.

    Real environment variables always win, so anything you exported in the shell is
    never clobbered. The parser skips blank lines and ``#`` comments, tolerates a
    leading ``export``, and strips surrounding single/double quotes."""
    env_path = path or (Path.cwd() / ".env")
    if not env_path.is_file():
        return []
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return []
    loaded: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


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
            required = " + ".join(
                (env_var, *PROVIDER_COMPANION_ENV_KEYS.get(provider, ()))
            )
            click.echo(f"    - {required}  (for --provider {provider})")
        return

    click.echo("  Detected provider keys:")
    for provider, env_var in available:
        click.echo(f"    - {provider} ({env_var} is set)")

    chosen_provider, _ = available[0]
    suggested_command = (
        f"ifixai run "
        f"--provider {chosen_provider} "
        f"--grounding fixture "
        f"--mode standard"
    )

    click.echo()
    click.echo(click.style("Recommended: guided setup (writes ifixai.yaml):", bold=True))
    click.echo("  ifixai setup")
    click.echo()
    click.echo(
        click.style("Best fidelity, test your real deployed agent:", bold=True)
    )
    click.echo(
        "  ifixai run --provider http --endpoint <base-url/v1> --grounding sut --mode standard"
    )
    click.echo(
        "  (probes the deployed agent's real tools + governance; `ifixai setup` saves the "
        "provider + endpoint, but the token, --auth-method, and headers still go on each run)"
    )
    click.echo()
    click.echo(click.style("Or replicate the bare model beneath it:", bold=True))
    click.echo(f"  {suggested_command}")
    click.echo()
    click.echo(
        "Mode defaults to 'standard' (CI-friendly, no hand-built fixture required). With a "
        "second distinct-provider key present it auto-pairs a cross-vendor judge; with only "
        "one key it refuses to run unless you pass --eval-mode self (a biased self-judge). "
        "Pass --mode full only for reference-grade runs with a hand-built fixture and a "
        "multi-judge ensemble."
    )

    if not non_interactive:
        click.confirm("Setup looks correct?", default=True, abort=False)
