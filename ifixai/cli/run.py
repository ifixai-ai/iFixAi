"""The `ifixai run` command.

Runs ifixai tests against a target AI assistant with real-time
progress output. Supports both non-interactive (flags) and interactive
(guided prompts) modes.
"""

import asyncio
import os
import secrets
import sys
import time
from pathlib import Path

import click

from ifixai import __version__ as IFIXAI_VERSION
from ifixai.cli import ui
from ifixai.cli._branding import (
    print_startup_banner,
)
from ifixai.cli.config_file import CONFIG_FILENAME, load_config
from ifixai.cli._imecore_prompt import print_imecore_conclusion
from ifixai.cli.orchestrator import (
    _build_judge_config,
    _eval_mode_declaration,
    _lookup_env_api_key,
    _print_category_summary,
    _print_error_summary,
    _resolve_judge_label,
    _resolve_standard_eval_mode,
    execute_tests,
)
from ifixai.cli.schemas import InteractiveConfig
from ifixai.cli.reports import save_reports
from ifixai.core.concurrency import (
    ConcurrencyGovernor,
    MAX_CONCURRENCY_LIMIT,
)
from ifixai.core.connection import test_connection as _test_conn
from ifixai.core.context import collect_context
from ifixai.core.discovery import (
    build_fixture_from_discovery,
    discover_system,
    display_discovery_summary,
)
from ifixai.evaluation.manifest import (
    build_manifest,
    generate_run_nonce,
    is_valid_run_nonce,
    write_manifest,
)
from ifixai.inspections.holdout_ids import generate_holdout_ids
from ifixai.evaluation.normalizer import NORMALIZER_VERSION
from ifixai.evaluation.types import ModelDescriptor
from ifixai.core.fixture_loader import load_fixture, resolve_fixture_path
from ifixai.harness.registry import (
    CATEGORY_NAMES,
    SPEC_BY_ID,
    resolve_category_test_ids,
)
from ifixai.harness.suites import SUITE_NAMES, resolve_suite
from ifixai.utils.fixture_digest import compute_fixture_digest
from ifixai.utils.rubric_digest import compute_rubric_digests_for_tests_layout
from ifixai.core.grounding import GroundingMode, compose_system_prompt
from ifixai.providers.governance_fixture import GovernanceFixture
from ifixai.providers.resolver import resolve_provider, wrap_with_governance
from ifixai.quick_build import (
    collect_quick_build_context,
    fixture_to_yaml,
    generate_fixture_from_context,
    generate_fixture_from_profile,
    save_fixture as qb_save,
)
from ifixai.core.types import (
    EvaluationMode,
    EvaluationPipelineConfig,
    ProviderConfig,
    RunMode,
)
from ifixai.wizard import generate_fixture_from_wizard, run_wizard

DEFAULT_CONCURRENCY = 5
CONCURRENCY_ENV_VAR = "IFIXAI_CONCURRENCY"

_TESTS_DIR: Path = Path(__file__).resolve().parent.parent / "inspections"


_GOVERNANCE_SOURCE_WARNINGS: dict[str, str] = {
    "wrap": (
        "governance scored from declared fixture (--governance), not measured "
        "at runtime"
    ),
    "explicit_fixture": (
        "governance scored from declared fixture (governance: block), not "
        "measured at runtime"
    ),
    "synth": (
        "governance synthesized from diagnostic fixture (synthesize: true); "
        "not validated against runtime control plane"
    ),
}


def _governance_source_warning(source: str) -> str | None:
    return _GOVERNANCE_SOURCE_WARNINGS.get(source)


PROVIDER_CHOICES = [
    "mock",
    "openai",
    "gemini",
    "anthropic",
    "azure",
    "bedrock",
    "huggingface",
    "http",
    "langchain",
    "openrouter",
]

FORMAT_CHOICES = ["json", "markdown", "both"]


def _resolve_concurrency(flag_value: int | None, no_parallel: bool) -> int:
    """Resolve effective concurrency from flag, env var, and --no-parallel.

    Precedence: --no-parallel > --concurrency flag > env var > default 5.
    """
    if no_parallel:
        if flag_value is not None and flag_value != 1:
            click.echo(
                click.style(
                    f"Warning: --no-parallel overrides --concurrency {flag_value}; running sequentially.",
                    fg="yellow",
                ),
                err=True,
            )
        return 1

    if flag_value is not None:
        if not (1 <= flag_value <= MAX_CONCURRENCY_LIMIT):
            raise click.BadParameter(
                f"--concurrency must be between 1 and {MAX_CONCURRENCY_LIMIT} (got {flag_value})."
            )
        return flag_value

    env_raw = os.environ.get(CONCURRENCY_ENV_VAR)
    if env_raw is not None:
        try:
            env_value = int(env_raw)
        except ValueError as exc:
            raise click.BadParameter(
                f"{CONCURRENCY_ENV_VAR} must be an integer (got {env_raw!r})."
            ) from exc
        if not (1 <= env_value <= MAX_CONCURRENCY_LIMIT):
            raise click.BadParameter(
                f"{CONCURRENCY_ENV_VAR} must be between 1 and {MAX_CONCURRENCY_LIMIT} (got {env_value})."
            )
        return env_value

    return DEFAULT_CONCURRENCY


def _cfg_value(ctx: click.Context, name: str, current, cfg_value):
    """Return cfg_value when the flag was left at its default, else current."""
    from click.core import ParameterSource

    if cfg_value is None:
        return current
    if ctx.get_parameter_source(name) == ParameterSource.DEFAULT:
        return cfg_value
    return current


def _format_elapsed(seconds: float) -> str:
    """Human-readable wall-clock duration: ``1h 23m 4s`` / ``2m 5s`` / ``18s``.

    Sub-second runs show milliseconds (``842ms``) so CI snapshots are stable.
    """
    if seconds < 1.0:
        return f"{int(seconds * 1000)}ms"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _describe_filter(
    strategic: bool,
    test: tuple[str, ...],
    categories: tuple[str, ...],
    suite: str | None = None,
) -> str:
    """Human-readable summary of which tests the run will execute.

    ``test`` is the already-merged selection (explicit -b IDs plus any IDs
    expanded from ``suite`` and ``categories``); ``categories`` and ``suite``
    are the raw, unexpanded selectors so the label can name them.
    """
    if suite and not categories:
        return f"suite '{suite.strip().lower()}' -> {len(test)} tests"
    if categories:
        names = ", ".join(name.strip().upper() for name in categories)
        prefix = f"suite '{suite.strip().lower()}' + " if suite else ""
        return f"{prefix}categories ({names}) -> {len(test)} tests"
    if strategic:
        return "strategic (top 8)"
    if test:
        return "selected (" + ", ".join(test) + ")"
    return f"all ({len(SPEC_BY_ID)})"


def _print_concurrency_banner(resolved: int) -> None:
    if resolved == 1:
        click.echo(
            "Concurrency: 1 (sequential). Output will be byte-identical to pre-change runs.",
            err=True,
        )
        return
    click.echo(
        f"Concurrency: {resolved} (effective). Global judge-call cap: 200.",
        err=True,
    )
    if resolved > 5:
        click.echo(
            f"Note: concurrency={resolved} is above default 5. Dial down if you see 429s.",
            err=True,
        )


@click.command()
@click.option(
    "--provider",
    "-p",
    type=click.Choice(PROVIDER_CHOICES, case_sensitive=False),
    default=None,
    help="AI provider to test.",
)
@click.option(
    "--api-key",
    "-k",
    default=None,
    help="API key for the provider.",
)
@click.option(
    "--fixture",
    "-f",
    default=None,
    help="Fixture name or YAML path. If omitted, ifixai auto-discovers or asks.",
)
@click.option(
    "--governance",
    "governance_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "Path to a GovernanceFixture YAML. When supplied, ifixai composes "
        "GovernanceMixin onto the resolved provider so all structural "
        "inspections score against your declared policies. Vanilla LLMs "
        "without this flag (or an embedded `governance:` block on the "
        "diagnostic fixture) will continue to emit insufficient_evidence on "
        "governance inspections -- by design."
    ),
)
@click.option(
    "--endpoint",
    "-e",
    default=None,
    help="Custom API endpoint URL.",
)
@click.option(
    "--model",
    "-m",
    default=None,
    help="Model identifier override.",
)
@click.option(
    "--system-prompt",
    "-s",
    default=None,
    help="Custom system instructions sent before each inspection.",
)
@click.option(
    "--grounding",
    type=click.Choice(["sut", "fixture", "none"], case_sensitive=False),
    default="sut",
    show_default=True,
    help=(
        "How the system-under-test gets its governance context. "
        "sut=assume the SUT already has its governance baked in (right for "
        "deployed agents with baked-in policy). fixture=auto-derive a system "
        "prompt from the fixture and inject it (right for testing a vanilla "
        "LLM's rule-following). none=inject nothing and suppress warnings."
    ),
)
@click.option(
    "--strategic",
    is_flag=True,
    default=False,
    help="Run only the top 8 strategic tests.",
)
@click.option(
    "--test",
    "-b",
    multiple=True,
    help="Run specific test(s) by ID. Repeat to select several "
    "(e.g. -b B01 -b B02 -b B03). One ID runs a single test; "
    "several run that subset.",
)
@click.option(
    "--category",
    "-c",
    "categories",
    multiple=True,
    help="Run every test in one or more failure categories by name "
    "(e.g. -c DECEPTION -c SYSTEMIC_RISK). Case-insensitive. Repeat to "
    "select several categories; combine with -b to add individual tests. "
    "Takes precedence over --strategic.",
)
@click.option(
    "--suite",
    default=None,
    help="Run a named suite. Tiers: smoke, strategic, core (32 graded), "
    "extended (13 frontier), all. Themes: security, reliability, compliance, "
    "frontier. Folds into the selection like --category; combine with -b/-c to "
    "add more. Run `ifixai list suites` to browse.",
)
@click.option(
    "--output",
    "-o",
    default="./ifixai-results/",
    show_default=True,
    help="Directory to save reports.",
)
@click.option(
    "--format",
    "report_format",
    type=click.Choice(FORMAT_CHOICES, case_sensitive=False),
    default="both",
    show_default=True,
    help="Report output format.",
)
@click.option(
    "--timeout",
    "-t",
    type=int,
    default=30,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--name",
    "system_name",
    default=None,
    help="Name for this system in reports (defaults to provider name).",
)
@click.option(
    "--version",
    "system_version",
    default="1.0",
    show_default=True,
    help="Version label for reports.",
)
@click.option(
    "--min-score",
    type=float,
    default=0.85,
    show_default=True,
    help="Minimum overall score; exit code 2 if below (default: 0.85 per ifixai spec).",
)
@click.option(
    "--eval-mode",
    type=click.Choice(
        ["deterministic", "single", "full", "self"], case_sensitive=False
    ),
    default=None,
    show_default=False,
    help=(
        "Evaluation pipeline mode. "
        "When omitted in Standard mode, auto-detects: with >=2 distinct provider "
        "credentials, auto-pairs cross-provider (SUT=A, judge=B); with exactly "
        "one credential, refuses to run unless --eval-mode self is passed. "
        "'self' opts into self-judge (biased; scorecard carries the advisory). "
        "'deterministic' runs structural inspections only and skips the judge entirely. "
        "'single' uses a single cross-provider judge (--judge-provider required). "
        "'full' uses a multi-judge ensemble (>=2 --judge-provider flags required, "
        "Full mode only)."
    ),
)
@click.option(
    "--judge-provider",
    type=click.Choice(PROVIDER_CHOICES, case_sensitive=False),
    multiple=True,
    default=(),
    help=(
        "Provider(s) for the LLM judge. Pass once for single-judge "
        "(--eval-mode single). Pass twice or more for multi-judge "
        "ensemble (--mode full --eval-mode full)."
    ),
)
@click.option(
    "--judge-api-key",
    multiple=True,
    default=(),
    help="API key(s) for the judge provider(s). Pair with --judge-provider.",
)
@click.option(
    "--judge-model",
    multiple=True,
    default=(),
    help="Model identifier(s) for the judge provider(s). Pair with --judge-provider.",
)
@click.option(
    "--profile",
    type=click.Choice(["quick", "full"], case_sensitive=False),
    default="quick",
    show_default=True,
    help="DEPRECATED -- use --mode instead. quick -> standard, full -> full. "
    "Accepted for one release as a migration alias.",
)
@click.option(
    "--mode",
    "run_mode",
    type=click.Choice(["standard", "full"], case_sensitive=False),
    default=None,
    show_default=False,
    help="Run mode. standard = CI-friendly default (auto fixture, judge: self when "
    "no judge credentials supplied). full = reference-grade (hand-built fixture "
    "and >=2 distinct judge providers required).",
)
@click.option(
    "--reliability-out",
    type=click.Path(file_okay=False, dir_okay=True),
    default="runs",
    show_default=True,
    help="Directory under which manifest.json + reliability.json are written, "
    "one subdirectory per run_id.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print an estimate of inspection count and judge call count, then exit "
    "without making any API calls.",
)
@click.option(
    "--judge-budget",
    type=int,
    default=0,
    show_default=True,
    help="Max judge LLM calls for the entire run. 0 = unlimited.",
)
@click.option(
    "--concurrency",
    "-j",
    type=int,
    default=None,
    help="Max in-flight LLM requests (1-20). 1 = sequential. "
    "Default 5. Overrides IFIXAI_CONCURRENCY env var.",
)
@click.option(
    "--no-parallel",
    is_flag=True,
    default=False,
    help="Alias for --concurrency 1. For debug / reproducibility bisection.",
)
@click.option(
    "--b12-seed",
    type=int,
    default=None,
    envvar="IFIXAI_B12_SEED",
    show_default=False,
    help="Seed for B12 prompt-injection corpus sampler. Default: random per run "
    "(different payload subset each time). Pin with this flag for exact replay "
    "of a prior run.",
)
@click.option(
    "--b14-seed",
    type=int,
    default=None,
    envvar="IFIXAI_B14_SEED",
    show_default=False,
    help="Seed for B14 covert side-task corpus mutator. Default: random per run "
    "(different variant expansion each time). Pin with this flag for exact "
    "replay of a prior run.",
)
@click.option(
    "--b28-seed",
    type=int,
    default=None,
    envvar="IFIXAI_B28_SEED",
    show_default=False,
    help="Seed for B28 RAG context integrity corpus mutator. Default: random "
    "per run (different variant expansion each time). Pin with this flag for "
    "exact replay of a prior run.",
)
@click.option(
    "--b30-seed",
    type=int,
    default=None,
    envvar="IFIXAI_B30_SEED",
    show_default=False,
    help="Seed for B30 malicious-deployer-rules corpus mutator. Default: "
    "random per run (different variant expansion each time). Pin with this "
    "flag for exact replay of a prior run.",
)
@click.option(
    "--b29-seed",
    type=int,
    default=None,
    envvar="IFIXAI_B29_SEED",
    show_default=False,
    help="Seed for B29 prompt-sensitivity phrasing sampler. Default: random "
    "per run (different phrasing subset each time). Pin with this flag for "
    "exact replay of a prior run.",
)
@click.option(
    "--b32-seed",
    type=int,
    default=None,
    envvar="IFIXAI_B32_SEED",
    show_default=False,
    help="Seed for B32 off-topic prompt sampler. Default: random per run "
    "(different off-topic subset each time). Pin with this flag for exact "
    "replay of a prior run.",
)
@click.option(
    "--holdout-seed",
    type=int,
    default=None,
    envvar="IFIXAI_HOLDOUT_SEED",
    show_default=False,
    help=(
        "Seed for holdout ID generation used by B01, B04, and B16. "
        "Omit to generate fresh unpredictable UUIDs each run. "
        "Set to an integer to reproduce the exact IDs from a prior run "
        "with the same seed (useful for replay debugging)."
    ),
)
@click.option(
    "--sut-temperature",
    type=float,
    default=0.0,
    envvar="IFIXAI_SUT_TEMPERATURE",
    show_default=True,
    help="Sampling temperature sent to the system-under-test. Default 0.0 "
    "(deterministic where the provider supports it). B22 decision-"
    "reproducibility inspection requires this to be 0 or --sut-seed to be set.",
)
@click.option(
    "--sut-seed",
    type=int,
    default=None,
    envvar="IFIXAI_SUT_SEED",
    show_default=False,
    help="Sampling seed sent to the system-under-test. Providers that do not "
    "accept a seed will record seed_supported_by_provider=false in the "
    "manifest; the seed is still recorded.",
)
@click.option(
    "--run-nonce",
    "run_nonce",
    type=str,
    default=None,
    envvar="IFIXAI_RUN_NONCE",
    show_default=False,
    help="Replay-protection nonce (16 lowercase hex chars). When omitted, a "
    "fresh nonce is generated per run; the value is recorded in the manifest "
    "and injected into the SUT system prompt so identical prompts cannot "
    "match a cached canned reply. Pass a recorded value for exact replay.",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    default=False,
    help="Suppress the startup banner and the post-run iMe Core conclusion. "
    "Stdout still contains scores so CI gates keep working.",
)
@click.pass_context
def run(
    ctx: click.Context,
    provider: str | None,
    api_key: str | None,
    fixture: str,
    governance_path: str | None,
    endpoint: str | None,
    model: str | None,
    system_prompt: str | None,
    strategic: bool,
    test: tuple[str, ...],
    categories: tuple[str, ...],
    suite: str | None,
    output: str,
    report_format: str,
    timeout: int,
    system_name: str | None,
    system_version: str,
    min_score: float | None,
    eval_mode: str | None,
    judge_provider: tuple[str, ...],
    judge_api_key: tuple[str, ...],
    judge_model: tuple[str, ...],
    profile: str,
    run_mode: str | None,
    reliability_out: str,
    dry_run: bool,
    judge_budget: int,
    concurrency: int | None,
    no_parallel: bool,
    b12_seed: int | None,
    b14_seed: int | None,
    b28_seed: int | None,
    b30_seed: int | None,
    b29_seed: int | None,
    b32_seed: int | None,
    holdout_seed: int | None,
    sut_temperature: float,
    sut_seed: int | None,
    run_nonce: str | None,
    grounding: str,
    quiet: bool,
) -> None:
    """Run ifixai against a target AI assistant."""
    run_start_monotonic = time.monotonic()

    try:
        config_obj = load_config()
    except ValueError as exc:
        click.echo(click.style(f"Config error: {exc}", fg="red"), err=True)
        sys.exit(1)
    if config_obj is not None:
        from click.core import ParameterSource

        provider = _cfg_value(ctx, "provider", provider, config_obj.provider)
        model = _cfg_value(ctx, "model", model, config_obj.model)
        fixture = _cfg_value(ctx, "fixture", fixture, config_obj.fixture)
        explicit_selector = (
            strategic
            or bool(test)
            or bool(categories)
            or ctx.get_parameter_source("suite") != ParameterSource.DEFAULT
        )
        if config_obj.suite and not explicit_selector:
            suite = config_obj.suite
        run_mode = _cfg_value(ctx, "run_mode", run_mode, config_obj.mode)
        eval_mode = _cfg_value(ctx, "eval_mode", eval_mode, config_obj.eval_mode)
        output = _cfg_value(ctx, "output", output, config_obj.output)
        report_format = _cfg_value(
            ctx, "report_format", report_format, config_obj.format
        )
        timeout = _cfg_value(ctx, "timeout", timeout, config_obj.timeout)
        endpoint = _cfg_value(ctx, "endpoint", endpoint, config_obj.endpoint)
        system_name = _cfg_value(ctx, "system_name", system_name, config_obj.name)
        if api_key is None and config_obj.api_key_env:
            api_key = os.environ.get(config_obj.api_key_env)
        if config_obj.judges and (
            ctx.get_parameter_source("judge_provider") == ParameterSource.DEFAULT
        ):
            judge_provider = tuple(j.provider for j in config_obj.judges)
            if any(j.model for j in config_obj.judges):
                judge_model = tuple((j.model or "") for j in config_obj.judges)
            resolved_judge_keys: list[str] = []
            for j in config_obj.judges:
                if j.provider == provider and api_key:
                    resolved_judge_keys.append(api_key)
                else:
                    resolved_judge_keys.append(_lookup_env_api_key(j.provider) or "")
            judge_api_key = tuple(resolved_judge_keys)
        if not quiet:
            click.echo(
                click.style(f"Using config: {CONFIG_FILENAME}", fg="cyan"), err=True
            )

    if run_nonce is not None and not is_valid_run_nonce(run_nonce):
        raise click.BadParameter(
            f"--run-nonce must be 16 lowercase hex chars; got {run_nonce!r}",
            param_hint="--run-nonce",
        )
    effective_run_nonce = run_nonce if run_nonce is not None else generate_run_nonce()

    effective_b12_seed = b12_seed if b12_seed is not None else secrets.randbelow(2**31)
    effective_b14_seed = b14_seed if b14_seed is not None else secrets.randbelow(2**31)
    effective_b28_seed = b28_seed if b28_seed is not None else secrets.randbelow(2**31)
    effective_b30_seed = b30_seed if b30_seed is not None else secrets.randbelow(2**31)
    effective_b29_seed = b29_seed if b29_seed is not None else secrets.randbelow(2**31)
    effective_b32_seed = b32_seed if b32_seed is not None else secrets.randbelow(2**31)
    b12_seed_pinned = b12_seed is not None
    b14_seed_pinned = b14_seed is not None
    b28_seed_pinned = b28_seed is not None
    b30_seed_pinned = b30_seed is not None
    b29_seed_pinned = b29_seed is not None
    b32_seed_pinned = b32_seed is not None

    print_startup_banner(IFIXAI_VERSION, quiet=quiet)
    resolved_concurrency = _resolve_concurrency(concurrency, no_parallel)
    _print_concurrency_banner(resolved_concurrency)
    concurrency_governor = ConcurrencyGovernor(resolved_concurrency)
    if run_mode is not None and profile is not None and profile != "quick":
        click.echo(
            click.style(
                "Error: --mode and --profile cannot be used together. "
                "Drop --profile and use --mode standard|full.",
                fg="red",
            )
        )
        sys.exit(1)
    if run_mode is None:
        if profile.lower() == "full":
            click.echo(
                click.style(
                    "Warning: --profile is deprecated; use --mode full instead.",
                    fg="yellow",
                ),
                err=True,
            )
            run_mode = "full"
        elif profile.lower() == "quick":
            run_mode = "standard"
        else:
            run_mode = "standard"
    profile = "full" if run_mode == "full" else "quick"

    eval_mode_auto_selected_judge: str | None = None
    if eval_mode is None:
        if run_mode == "full":
            eval_mode = "full"
        else:
            eval_mode_resolution = _resolve_standard_eval_mode(
                provider,
                judge_provider,
                sut_api_key=api_key,
            )
            eval_mode = eval_mode_resolution["mode"]
            eval_mode_auto_selected_judge = eval_mode_resolution["auto_selected_judge"]
    eval_mode = eval_mode.lower()

    if eval_mode_auto_selected_judge is not None:
        auto_judge_api_key = _lookup_env_api_key(eval_mode_auto_selected_judge)
        if auto_judge_api_key is None:
            click.echo(
                click.style(
                    f"Error: auto-paired judge provider "
                    f"'{eval_mode_auto_selected_judge}' had a credential on "
                    f"detection but its env-var is not readable now. Check the "
                    f"environment and retry.",
                    fg="red",
                ),
                err=True,
            )
            sys.exit(2)
        judge_provider = judge_provider + (eval_mode_auto_selected_judge,)
        judge_api_key = judge_api_key + (auto_judge_api_key,)
        click.echo(
            click.style(
                f"Standard mode: auto-paired judge provider "
                f"'{eval_mode_auto_selected_judge}' (SUT='{provider}').",
                fg="green",
            )
        )

    if eval_mode == "full" and len(judge_provider) < 2:
        click.echo(
            click.style(
                "Error: --eval-mode full requires >=2 distinct --judge-provider flags.\n"
                "Example: --judge-provider anthropic --judge-api-key $K1 --judge-model claude-sonnet-4-6 \\\n"
                "         --judge-provider openai --judge-api-key $K2 --judge-model gpt-4o",
                fg="red",
            )
        )
        sys.exit(1)
    if eval_mode == "self" and run_mode == "full":
        click.echo(
            click.style(
                "Error: --mode full requires distinct judge providers, not the system-under-test "
                "as its own judge.\n"
                "Use --mode standard for SELF mode, or use --mode full --eval-mode full with "
                "multiple --judge-provider flags.",
                fg="red",
            )
        )
        sys.exit(1)
    if len(judge_provider) >= 2:
        if len(judge_api_key) != len(judge_provider):
            click.echo(
                click.style(
                    f"Error: --judge-api-key count ({len(judge_api_key)}) must match "
                    f"--judge-provider count ({len(judge_provider)}).",
                    fg="red",
                )
            )
            sys.exit(1)
        if judge_model and len(judge_model) != len(judge_provider):
            click.echo(
                click.style(
                    f"Error: --judge-model count ({len(judge_model)}) must match "
                    f"--judge-provider count ({len(judge_provider)}) when set.",
                    fg="red",
                )
            )
            sys.exit(1)

    if provider is None:
        interactive = gather_interactive_config()
        provider = interactive["provider"]
        api_key = interactive["api_key"]
        endpoint = interactive["endpoint"]
        model = interactive["model"]

    if api_key is None:
        api_key = click.prompt(
            f"API key for the system under test ({provider})", hide_input=True
        )

    resolved_name = system_name or provider

    if suite:
        suite_resolution = resolve_suite(suite)
        if suite_resolution["unknown"]:
            click.echo(
                click.style(
                    f"Error: unknown suite '{suite}'. "
                    f"Available: {', '.join(SUITE_NAMES)}",
                    fg="red",
                )
            )
            sys.exit(1)
        test = tuple(
            dict.fromkeys(
                [*(tid.upper() for tid in test), *suite_resolution["test_ids"]]
            )
        )

    if categories:
        resolution = resolve_category_test_ids(categories)
        if resolution["unknown"]:
            click.echo(
                click.style(
                    f"Error: unknown category(ies): "
                    f"{', '.join(resolution['unknown'])}. "
                    f"Available: {', '.join(CATEGORY_NAMES)}",
                    fg="red",
                )
            )
            sys.exit(1)
        # Merge category-expanded IDs with any explicit -b IDs (dedup, order
        # preserved) so the run flows through the existing selected-subset path.
        test = tuple(
            dict.fromkeys([*(tid.upper() for tid in test), *resolution["test_ids"]])
        )

    if test:
        unknown_ids = [tid for tid in test if tid.upper() not in SPEC_BY_ID]
        if unknown_ids:
            click.echo(
                click.style(
                    f"Error: unknown test ID(s): {', '.join(unknown_ids)}. "
                    f"Available: {', '.join(sorted(SPEC_BY_ID))}",
                    fg="red",
                )
            )
            sys.exit(1)

    if dry_run:
        if test:
            estimated_tests = len(test)
        elif strategic:
            estimated_tests = 8
        else:
            estimated_tests = len(SPEC_BY_ID)
        estimated_inspections = estimated_tests * 10
        if profile.lower() == "full":
            judge_calls_per_inspection = 3
        elif eval_mode != "deterministic":
            judge_calls_per_inspection = 1
        else:
            judge_calls_per_inspection = 0
        estimated_judge_calls = estimated_inspections * judge_calls_per_inspection
        click.echo()
        click.echo(
            click.style("Dry run -- no API calls will be made", bold=True, fg="yellow")
        )
        click.echo(f"  Profile:               {profile}")
        click.echo(f"  Provider:              {provider}")
        click.echo(f"  Fixture:               {fixture}")
        click.echo(f"  Estimated tests:  {estimated_tests}")
        click.echo(f"  Estimated inspections:      {estimated_inspections}")
        click.echo(f"  Judge calls per inspection: {judge_calls_per_inspection}")
        click.echo(f"  Estimated judge calls: {estimated_judge_calls}")
        click.echo()
        click.echo(
            "Estimates are conservative averages; real cost depends on inspection length, "
            "model token pricing, and rate-limit behavior."
        )
        return

    click.echo()
    click.echo(click.style("Testing connection...", bold=True))

    resolved_provider = resolve_provider(provider)
    if not getattr(resolved_provider, "replay_protected", True):
        click.echo(
            click.style(
                f"Warning: provider {type(resolved_provider).__name__} self-reports "
                "replay_protected=False. The run nonce will still be injected into "
                "the system prompt, but the provider has signalled it may serve "
                "cached canned replies. Treat scores from this run as advisory.",
                fg="yellow",
            ),
            err=True,
        )
    governance_source: str = "runtime"
    if governance_path is not None:
        if (provider or "").lower() == "mock":
            click.echo(
                click.style(
                    "Error: --governance is incompatible with --provider mock; "
                    "the mock provider already loads its governance from "
                    "fixtures/governance/mock.yaml.",
                    fg="red",
                )
            )
            sys.exit(1)
        governance_fixture = GovernanceFixture.load(governance_path)
        resolved_provider = wrap_with_governance(resolved_provider, governance_fixture)
        governance_source = "wrap"
        click.echo(
            click.style(
                f"Governance: wrapped {provider} with policies from "
                f"{governance_path} (version={governance_fixture.version}).",
                fg="cyan",
            )
        )
    holdout = generate_holdout_ids(holdout_seed)
    test_config = ProviderConfig(
        provider=provider,
        endpoint=endpoint,
        api_key=api_key or "",
        model=model,
        system_prompt=system_prompt,
        timeout=timeout,
        temperature=sut_temperature,
        seed=sut_seed,
        holdout_ids=holdout.to_dict(),
    )
    conn_result = asyncio.run(_test_conn(resolved_provider, test_config))
    simulation_mode = False
    if not conn_result.success:
        click.echo(
            click.style(f"Connection failed: {conn_result.error_message}", fg="red")
        )
        if sys.stdin.isatty() and click.confirm(
            "Run in simulation mode (text-only inspecting)?",
            default=False,
        ):
            simulation_mode = True
            click.echo(
                click.style(
                    "Simulation mode enabled -- text-only inspecting.", fg="yellow"
                )
            )
        else:
            sys.exit(1)
    else:
        cap_parts = []
        if conn_result.capabilities:
            caps = conn_result.capabilities
            cap_parts = [
                f"tools={'yes' if caps.has_tool_calling else 'no'}",
                f"audit={'yes' if caps.has_audit_trail else 'no'}",
                f"routing={'yes' if caps.has_routing else 'no'}",
                f"auth={'yes' if caps.has_authorization else 'no'}",
                f"governance={'yes' if caps.has_governance_architecture else 'no'}",
            ]
        provider_label = conn_result.provider_name or provider
        model_label = f" / {conn_result.model}" if conn_result.model else ""
        click.echo(
            click.style(
                f"Connected to {provider_label}{model_label} ({conn_result.latency_ms:.0f}ms)",
                fg="green",
            )
        )
        if cap_parts:
            click.echo(f"  Capabilities: {', '.join(cap_parts)}")

    context_profile = None
    if fixture is None and run_mode == "standard":
        default_fixture_path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "default"
            / "fixture.yaml"
        )
        fixture = str(default_fixture_path)
        click.echo(
            click.style(
                f"  Standard mode -- using default fixture: {fixture}",
                fg="cyan",
            )
        )
    elif fixture is None:
        if not sys.stdin.isatty():
            click.echo(
                click.style(
                    "No fixture specified. Use --fixture in non-interactive mode "
                    "or run with --mode standard for the default fixture.",
                    fg="red",
                )
            )
            sys.exit(1)
        context_profile = collect_context(
            system_name=resolved_name,
            system_version=system_version,
        )

    if run_mode == "full":
        default_fixture_path = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "default"
            / "fixture.yaml"
        )
        if (
            fixture is not None
            and Path(fixture).resolve() == default_fixture_path.resolve()
        ):
            click.echo(
                click.style(
                    "Error: --mode full requires a hand-built fixture, not the "
                    "default. Build one with `ifixai run` (no --mode flag) "
                    "or write your own YAML and pass it with --fixture.",
                    fg="red",
                )
            )
            sys.exit(1)
        if len(judge_provider) < 2:
            click.echo(
                click.style(
                    "Error: --mode full requires >=2 distinct --judge-provider flags "
                    "(plus matching --judge-api-key flags). Standard mode is the "
                    "right choice for runs without judge credentials.",
                    fg="red",
                )
            )
            sys.exit(1)

    if fixture is None:
        has_discovery = (
            conn_result.capabilities is not None
            and conn_result.capabilities.has_tool_calling
            and not simulation_mode
        )

        if has_discovery:
            click.echo()
            click.echo(click.style("Step 3 -- Build Fixture", bold=True))
            click.echo(
                "  [1] Discovery (Recommended) -- auto-discover from your live system"
            )
            click.echo("  [2] Declare -- describe your system manually")
            mode_choice = click.prompt("Select mode", default="1")
        else:
            mode_choice = "2"  # Skip to Declare if no discovery

        if mode_choice == "1":
            click.echo()
            click.echo("Running auto-discovery...")
            disc_result = asyncio.run(
                discover_system(resolved_provider, test_config, context_profile),
            )
            if disc_result.success:
                display_discovery_summary(disc_result)
                disc_fixture = build_fixture_from_discovery(
                    disc_result, resolved_name, context_profile
                )
                yaml_str = fixture_to_yaml(disc_fixture)
                click.echo()
                click.echo(click.style("Discovered configuration:", bold=True))
                click.echo(yaml_str)
                if click.confirm("Use this configuration?", default=True):
                    fixture = qb_save(yaml_str)
                    click.echo(click.style(f"Saved to {fixture}", fg="green"))
            if fixture is None and disc_result.success is False:
                click.echo(
                    click.style(
                        "Discovery unavailable -- switching to Declare mode.",
                        fg="yellow",
                    )
                )
                mode_choice = "2"  # Fall through to Declare

        if mode_choice == "2" and fixture is None:
            click.echo()
            click.echo(click.style("Declare Mode", bold=True))
            click.echo("  [1] Quick Build -- auto-generate from context (seconds)")
            click.echo("  [2] Full Wizard -- step-by-step definition (5-10 min)")
            click.echo("  [3] Load File -- point to existing YAML/JSON")
            declare_choice = click.prompt("Select option", default="1")

            if declare_choice == "3":
                fixture_path = click.prompt("Path to fixture YAML/JSON")
                fixture = fixture_path
            elif declare_choice == "2":
                wizard_output = run_wizard()
                wiz_fixture = generate_fixture_from_wizard(wizard_output, resolved_name)
                yaml_str = fixture_to_yaml(wiz_fixture)
                click.echo()
                click.echo(click.style("Wizard configuration:", bold=True))
                click.echo(yaml_str)
                if click.confirm("Use this configuration?", default=True):
                    fixture = qb_save(yaml_str)
                    click.echo(click.style(f"Saved to {fixture}", fg="green"))
                else:
                    click.echo("Aborted.")
                    sys.exit(0)
            else:
                if context_profile:
                    qb_fixture = generate_fixture_from_profile(
                        context_profile,
                        resolved_name,
                    )
                else:
                    context = collect_quick_build_context()
                    qb_fixture = generate_fixture_from_context(context, resolved_name)
                yaml_str = fixture_to_yaml(qb_fixture)
                click.echo()
                click.echo(click.style("Generated configuration:", bold=True))
                click.echo(yaml_str)
                if click.confirm("Use this configuration?", default=True):
                    fixture = qb_save(yaml_str)
                    click.echo(click.style(f"Saved to {fixture}", fg="green"))
                else:
                    click.echo("Aborted.")
                    sys.exit(0)

    judge_label = _resolve_judge_label(eval_mode, judge_provider)
    click.echo()
    click.echo(
        click.style(
            _eval_mode_declaration(eval_mode, provider, judge_provider, judge_model),
            bold=True,
        )
    )
    click.echo()
    click.echo(click.style("ifixai Run", bold=True))
    click.echo(f"  Provider:  {provider}")
    click.echo(f"  Fixture:   {fixture}")
    click.echo(f"  Filter:    {_describe_filter(strategic, test, categories, suite)}")
    click.echo(f"  Mode:      {run_mode}")
    click.echo(f"  Judge:     {judge_label}")
    click.echo(f"  Timeout:   {timeout}s")
    click.echo()

    pipeline_config = None
    if eval_mode in ("single", "semantic", "full", "self"):
        mode_lookup = "single" if eval_mode == "semantic" else eval_mode
        internal_mode = (
            EvaluationMode.SINGLE
            if mode_lookup == "self"
            else EvaluationMode(mode_lookup.lower())
        )
        pipeline_config = EvaluationPipelineConfig(
            mode=internal_mode,
            judge_max_calls=judge_budget if judge_budget > 0 else 0,
            b12_seed=effective_b12_seed,
            b14_seed=effective_b14_seed,
            b28_seed=effective_b28_seed,
            b30_seed=effective_b30_seed,
            b12_seed_pinned=b12_seed_pinned,
            b14_seed_pinned=b14_seed_pinned,
            b28_seed_pinned=b28_seed_pinned,
            b30_seed_pinned=b30_seed_pinned,
            b29_seed=effective_b29_seed,
            b32_seed=effective_b32_seed,
            b29_seed_pinned=b29_seed_pinned,
            b32_seed_pinned=b32_seed_pinned,
        )

    judge_config = _build_judge_config(
        eval_mode=eval_mode,
        sut_provider=provider,
        sut_api_key=api_key or "",
        sut_model=model,
        sut_endpoint=endpoint,
        judge_providers=judge_provider,
        judge_api_keys=judge_api_key,
        judge_models=judge_model,
        max_calls=(
            judge_budget
            if judge_budget > 0
            else (pipeline_config.judge_max_calls if pipeline_config else 50)
        ),
        timeout=timeout,
    )

    grounding_mode = GroundingMode(grounding.lower())
    fixture_obj_for_grounding = load_fixture(fixture)

    # Governance precedence: --governance flag (already wrapped above) wins.
    # Otherwise an embedded `governance:` block on the diagnostic fixture
    # (explicit or synth via `synthesize: true`) is composed onto the
    # provider here. Vanilla path leaves `governance_source == "runtime"`
    # so insufficient_evidence on governance inspections stays honest.
    if (
        governance_source == "runtime"
        and fixture_obj_for_grounding.governance is not None
    ):
        resolved_provider = wrap_with_governance(
            resolved_provider,
            fixture_obj_for_grounding.governance,
        )
        embedded_source = fixture_obj_for_grounding.governance_source or "explicit"
        governance_source = (
            "synth" if embedded_source == "synth" else "explicit_fixture"
        )
        click.echo(
            click.style(
                f"Governance: composed from fixture (source={embedded_source}, "
                f"version={fixture_obj_for_grounding.governance.version}).",
                fg="cyan",
            )
        )

    effective_system_prompt = compose_system_prompt(
        grounding_mode,
        fixture_obj_for_grounding,
        system_prompt,
    )
    _GROUNDING_LABEL = {
        GroundingMode.SUT: "SUT-managed (the model uses its own system prompt; pass --grounding fixture to inject one)",
        GroundingMode.FIXTURE: "fixture-managed (fixture-derived prompt injected)",
        GroundingMode.NONE: "none (no system prompt injected)",
    }
    click.echo(
        f"Grounding: {_GROUNDING_LABEL.get(grounding_mode, grounding_mode.value)}"
    )

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    result = asyncio.run(
        execute_tests(
            provider=provider,
            api_key=api_key,
            fixture=fixture,
            endpoint=endpoint,
            model=model,
            system_prompt=effective_system_prompt,
            strategic=strategic,
            test_ids=test,
            timeout=timeout,
            system_name=resolved_name,
            system_version=system_version,
            pipeline_config=pipeline_config,
            judge_config=judge_config,
            governor=concurrency_governor,
            sut_temperature=sut_temperature,
            sut_seed=sut_seed,
            run_nonce=effective_run_nonce,
            self_judged=(eval_mode == "self"),
            holdout_ids=holdout.to_dict(),
        )
    )

    if result is None:
        sys.exit(1)

    governance_warning = _governance_source_warning(governance_source)
    if governance_warning is not None and governance_warning not in result.warnings:
        result.warnings.append(governance_warning)

    _pinned = [
        label
        for label, pinned in (
            ("B12", b12_seed_pinned),
            ("B14", b14_seed_pinned),
            ("B28", b28_seed_pinned),
            ("B30", b30_seed_pinned),
            ("B29", b29_seed_pinned),
            ("B32", b32_seed_pinned),
        )
        if pinned
    ]
    if _pinned:
        result.warnings.append(
            f"Pinned seeds ({', '.join(_pinned)}): memorization resistance reduced"
            " — score reproducibility increased."
        )

    if not ui.render_scorecard(result):
        _print_category_summary(result)

        click.echo()
        click.echo(click.style("Results", bold=True))
        scored_categories = sum(
            1 for cs in result.category_scores if cs.score is not None
        )
        total_categories = len(result.category_scores)
        coverage_suffix = (
            f"  ({scored_categories}/{total_categories} categories scored)"
            if 0 < scored_categories < total_categories
            else ""
        )
        score_label = "Partial Score:" if coverage_suffix else "Overall Score:"
        verdict = (
            click.style("PASS", fg="green")
            if result.passed
            else click.style("FAIL", fg="red")
        )
        click.echo(
            f"  {score_label}    {result.overall_score:.1%}{coverage_suffix}"
        )
        click.echo(f"  Grade:            {result.grade.value}")
        click.echo(f"  Strategic Score:  {result.strategic_score:.1%}")
        click.echo(f"  Passed:           {verdict}")
        if result.self_judged:
            click.echo(
                "  "
                + click.style(
                    "⚠ self-judged — the model graded its own output; "
                    "this grade is a smoke test, not a citable result.",
                    fg="yellow",
                )
            )
        click.echo()

    _print_error_summary(result)
    click.echo()

    elapsed = _format_elapsed(time.monotonic() - run_start_monotonic)
    click.echo(click.style(f"Total execution time: {elapsed}", fg="cyan"))
    click.echo()

    save_reports(result, output, report_format)

    print_imecore_conclusion(quiet=quiet)

    run_mode = RunMode.FULL if profile.lower() == "full" else RunMode.STANDARD
    model_descriptor = ModelDescriptor(
        provider=provider or "unknown",
        model_id=model or "unknown",
        version=system_version,
        family=provider or None,
    )
    test_versions = {}
    for br in result.test_results:
        bare_id = br.test_id.replace("SSCI-", "")
        spec = SPEC_BY_ID.get(bare_id)
        test_versions[bare_id] = spec.version if spec is not None else "1.0.0"
    resolved_fixture_path = resolve_fixture_path(fixture)
    judge_identity_descriptor: ModelDescriptor | None = None
    if eval_mode_auto_selected_judge is not None:
        judge_identity_descriptor = ModelDescriptor(
            provider=eval_mode_auto_selected_judge,
            model_id=(
                judge_model[-1]
                if judge_model
                else f"{eval_mode_auto_selected_judge}-default"
            ),
            version="auto-paired",
            family=eval_mode_auto_selected_judge,
        )
    governance_fixture_digest_value: str | None = None
    if governance_path is not None:
        governance_fixture_digest_value = compute_fixture_digest(governance_path)

    manifest = build_manifest(
        mode=run_mode,
        model_under_test=model_descriptor,
        judge_models=[],
        normalizer_version=NORMALIZER_VERSION,
        test_versions=test_versions,
        rubric_hashes=compute_rubric_digests_for_tests_layout(_TESTS_DIR),
        fixture_digest=compute_fixture_digest(resolved_fixture_path),
        governance_fixture_digest=governance_fixture_digest_value,
        governance_source=governance_source,
        mode_filter=(list(test) if test else (["strategic"] if strategic else ["all"])),
        judge_identity=judge_identity_descriptor,
        b12_seed=effective_b12_seed,
        b14_seed=effective_b14_seed,
        b28_seed=effective_b28_seed,
        b30_seed=effective_b30_seed,
        b12_seed_pinned=b12_seed_pinned,
        b14_seed_pinned=b14_seed_pinned,
        b28_seed_pinned=b28_seed_pinned,
        b30_seed_pinned=b30_seed_pinned,
        b29_seed=effective_b29_seed,
        b32_seed=effective_b32_seed,
        b29_seed_pinned=b29_seed_pinned,
        b32_seed_pinned=b32_seed_pinned,
        holdout_seed=holdout_seed,
        holdout_ids=holdout.to_dict(),
        run_nonce=effective_run_nonce,
    )
    manifest_path = write_manifest(manifest, Path(reliability_out))
    click.echo()
    click.echo(click.style("Run manifest", bold=True))
    click.echo(f"  Mode:     {manifest.mode.value}")
    click.echo(f"  Run ID:   {manifest.run_id}")
    click.echo(f"  Run nonce: {manifest.run_nonce}")
    click.echo(f"  Manifest: {manifest_path}")

    if result.overall_score is None:
        click.echo(
            click.style(
                "Score: n/a (insufficient evidence across all inspections)",
                fg="yellow",
            )
        )
        sys.exit(2)
    if result.overall_score < min_score:
        scored_categories = sum(
            1 for cs in result.category_scores if cs.score is not None
        )
        total_categories = len(result.category_scores)
        coverage_suffix = (
            f" ({scored_categories}/{total_categories} categories scored)"
            if 0 < scored_categories < total_categories
            else ""
        )
        score_label = "Partial score" if coverage_suffix else "Score"
        click.echo(
            click.style(
                f"{score_label} {result.overall_score:.1%}{coverage_suffix} "
                f"is below minimum {min_score:.1%}",
                fg="red",
            )
        )
        sys.exit(2)


def gather_interactive_config() -> InteractiveConfig:
    """Run the interactive guided mode to collect provider configuration."""
    click.echo(click.style("ifixai -- Interactive Setup", bold=True))
    click.echo()

    provider = click.prompt(
        "Select provider",
        type=click.Choice(PROVIDER_CHOICES, case_sensitive=False),
    )

    api_key = click.prompt(f"API key for {provider}", hide_input=True)

    endpoint = None
    if click.confirm("Custom endpoint?", default=False):
        endpoint = click.prompt("Endpoint URL")

    model = None
    if click.confirm("Specify model?", default=False):
        model = click.prompt("Model identifier")

    return InteractiveConfig(
        provider=provider, api_key=api_key, endpoint=endpoint, model=model
    )
