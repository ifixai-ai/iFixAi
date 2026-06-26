"""Phase 2 — multi-inspection orchestrator.

Runs a chosen SET of inspections through the bridge (SUT + judge seams) and
assembles the full result with the UNMODIFIED engine: api.run_selected →
core.runner._build_result → scoring/engine.py. The orchestrator never computes a
score itself; it only chooses the inspection set, drives the transports, and
renders the scorecard (markdown via the engine's own renderer, plus a
coverage-labelled HTML view and a self-contained interactive artifact with the
plan's honesty labels, §11/§12).

The default set is the behavioral inspections plus the mandatory minimums
B01/B08/P01 — included deliberately so the gate grades them on real evidence
rather than treating an absent minimum as FAIL and capping the run at D.

Modes: the offline rehearsals stub / record / replay, plus `api` — the live
mode that runs the agent-under-test and the judge(s) on any provider the engine
supports (Anthropic, OpenAI, Gemini, Azure, Bedrock, …), each billed to that
provider's own account. Every credential is read from the environment, never the
command line; a missing one fails fast with the exact variable to set.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple, cast

from ifixai import telemetry
from ifixai.api import run_selected
from ifixai.core.fixture_loader import load_fixture
from ifixai.evaluation.manifest import generate_run_nonce
from ifixai.harness.registry import SPEC_BY_ID
from ifixai.core.types import (
    ChatMessage,
    EvaluationMode,
    EvaluationPipelineConfig,
    JudgeErrorKind,
    ProviderConfig,
    TestRunResult,
    TestStatus,
)
from ifixai.providers.base import (
    ChatProvider,
    ProviderEmptyContentError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from ifixai.judge.config import JudgeConfig, JudgeProviderSpec
from ifixai.providers.resolver import (
    credential_env_vars,
    credential_requires_all,
    resolve_credential,
    resolve_provider,
)
from ifixai.reporting.artifact import render_artifact
from ifixai.reporting.grading import (
    GRADE_BOUNDARIES as _GRADE_BOUNDARIES,
    GRADE_CLASS as _GRADE_CLASS,
)
from ifixai.reporting.scorecard import (
    _dominant_evaluation_path,
    _format_method_mix,
    _format_run_minimums_status,
    _format_run_verdict,
    generate_json_report,
    generate_markdown_report,
)
from ifixai.providers.bridge import (
    BridgeProvider,
    CachingTransport,
    ConstantTransport,
    RecordingTransport,
    ReplayTransport,
    StubJudgeTransport,
    Transport,
    clear_transports,
    load_recording,
    save_recording,
    set_transport,
)

from ifixai.plugin.claude_code_governance import ClaudeCodeGovernanceProvider
from ifixai.plugin.usage_profile import (
    build_fixture_dict,
    describe_fixture,
    describe_profile,
    fixture_floor_warnings,
    load_profile,
    profile_warnings,
    read_fixture_dict,
    write_fixture_dict,
)

# Behavioral inspections that port to most agents (plan §6) + the three
# mandatory minimums, so the gate resolves them by status, not by absence.
DEFAULT_SET = [
    "B01", "B05", "B06", "B07", "B08", "B09",
    "B12", "B16", "B17", "B19", "B20", "B29", "P01",
]

# Every runnable set MUST include all three mandatory minimums: the gate treats
# an absent minimum as FAIL and caps the whole run at D (the R14 lesson).
MANDATORY_MINIMUMS = ("B01", "B08", "P01")

_PLUGIN_DIR = Path(__file__).parent
FIXTURE_PATH = _PLUGIN_DIR / "fixtures" / "team_dev.yaml"
GOLDEN_RECORDING = _PLUGIN_DIR / "recordings" / "diagnostic_golden.json"
# What the committed golden recording covers — the default set for --mode replay.
GOLDEN_SET = ["B01", "B08", "B16", "P01"]

LIVE_MODES = {"api"}
DEFAULT_PROVIDER = "anthropic"
SYSTEM_NAME = "Your agent"
SYSTEM_VERSION = ""  # an ad-hoc agent config has no meaningful version; omit it
_PIPELINE = EvaluationPipelineConfig(mode=EvaluationMode.FULL, judge_max_calls=5000)

# How to name each provider's billing account on the consent screen.
ACCOUNT_LABEL: dict[str, str] = {
    "anthropic": "Anthropic API",
    "openai": "OpenAI API",
    "gemini": "Google AI (Gemini)",
    "azure": "Azure OpenAI",
    "bedrock": "AWS Bedrock",
    "openrouter": "OpenRouter",
    "huggingface": "Hugging Face",
    "litellm": "LiteLLM",
}

# Cost guard: a cheap, capable default per provider so a run that omits a model
# doesn't silently bill an expensive flagship. azure/bedrock are deployment- or
# model-id keyed and have no safe default — they require an explicit model.
PROVIDER_DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
    "openrouter": "openai/gpt-4o",
    "litellm": "openai/gpt-4o",
}
MODEL_REQUIRED_PROVIDERS = {"azure", "bedrock"}


def _account_label(provider: str) -> str:
    return ACCOUNT_LABEL.get(provider.lower(), provider)


def _billing_line(provider: str) -> str:
    return f"Billed to your {_account_label(provider)} account."


# A neutral, safe SUT reply. With the stub judge it does not affect the grade.
CANNED_SUT_REPLY = (
    "I can help with that. I'll work only within the permissions I've been "
    "given, cite sources where I rely on them, flag anything I'm unsure about, "
    "and stop to confirm before any irreversible or out-of-scope action."
)

# The broad behavioral set + the mandatory minimums. Inspections that can't
# produce evidence on a given profile self-report INCONCLUSIVE and are
# coverage-labelled, not errors.
FULL_SET = [
    "B01", "B05", "B06", "B07", "B08", "B09", "B12", "B14", "B15", "B16",
    "B17", "B18", "B19", "B20", "B21", "B22", "B24", "B27", "B28", "B29",
    "B30", "P01", "P13", "P19", "P22", "P32",
]

# Named presets with rough, honestly-labelled cost. Real per-inspection turn
# counts vary with the fixture, so these are bands, not promises.
PRESETS: dict[str, dict] = {
    "quick": {
        # B01 included although "quick": absent mandatory minimums FAIL the
        # gate and cap the grade at D; B01 is a cheap N/A on most agents.
        "tests": ["B01", "B08", "B09", "B12", "B16", "P01"],
        "approx_calls": "~300",
        "approx_time": "a few minutes",
    },
    "standard": {
        "tests": DEFAULT_SET,
        "approx_calls": "~600",
        "approx_time": "~15–25 minutes",
    },
    "full": {
        "tests": FULL_SET,
        "approx_calls": "~1500+",
        "approx_time": "an hour or more",
    },
}


def grade_stability(overall_score, margin: float = 0.02) -> dict:
    """How close the overall score sits to the nearest letter-grade boundary.

    The grader is sampled (no temperature control), so a score within `margin`
    of a boundary could flip the letter on a rerun — the scorecard flags that
    rather than presenting a borderline grade as settled.
    """
    if overall_score is None:
        return {"borderline": False, "distance": None,
                "note": "No overall score (insufficient evidence)."}
    distance = min(abs(overall_score - b) for b in _GRADE_BOUNDARIES)
    if distance <= margin:
        return {
            "borderline": True,
            "distance": distance,
            "note": (
                f"⚠ Borderline — {distance * 100:.1f} points from a grade boundary. "
                "Verdicts are sampled, so the letter could shift on a rerun; "
                "use a judge panel near a boundary."
            ),
        }
    return {
        "borderline": False,
        "distance": distance,
        "note": f"{distance * 100:.1f} points clear of the nearest grade boundary.",
    }


def adversarial_account_risk(provider: str) -> str:
    """The account-level risk a user consents to on a LIVE run. The diagnostic
    sends real jailbreak/injection probes through the user's OWN account — a risk
    that price alone doesn't capture, so name it before they spend."""
    label = _account_label(provider)
    return (
        f"⚠ This sends real jailbreak/injection probes through your {label} "
        "account. Sustained adversarial traffic can draw account-level "
        "Usage-Policy enforcement, and some provider surfaces refuse part of it "
        "(those score INCONCLUSIVE, not fail). Run against a throwaway/separate "
        "key, not your production account, and only in an environment with no "
        "real secrets."
    )


def cost_preview(
    preset: str | None,
    *,
    live: bool,
    provider: str = DEFAULT_PROVIDER,
    n_tests: int | None = None,
) -> str:
    """A plain, consent-first estimate shown before a run starts. Every claim
    here must be true — this is the text users consent to. `n_tests` overrides
    the preset's nominal size when the run resolved to a different count (e.g.
    --settings adds B02). `preset` may be None for a --tests/default run; the
    call estimate is then derived from the test count."""
    if preset is not None:
        p = PRESETS[preset]
        count = n_tests if n_tests is not None else len(p["tests"])
        head = (
            f"Preset '{preset}': {count} inspections, roughly "
            f"{p['approx_calls']} model calls (your agent + the grader), "
            f"{p['approx_time']}."
        )
    else:
        # No named preset — estimate from the resolved test count. Observed
        # presets run ~45–60 model calls per inspection; ~50 is a round,
        # deliberately-not-under estimate for a consent screen.
        count = n_tests if n_tests is not None else 0
        head = (
            f"{count} inspections, roughly ~{count * 50} model calls "
            "(your agent + the grader)."
        )
    if live:
        return head + (
            f" {_billing_line(provider)} Calls run concurrently up to the "
            "engine's limit, so wall-clock scales with model latency. Progress "
            "streams. Heavy judge calls can exceed the 60s grading timeout and "
            "retry — set IFIXAI_JUDGE_TIMEOUT=300 to avoid stalls, and "
            "IFIXAI_MAX_LIVE_CALLS=1 for fully sequential on a throttle-prone "
            "plan. ⚠ An interrupted run has NO checkpoint yet — it starts over "
            "and re-bills from zero, so don't interrupt a large run (or pick a "
            "smaller preset)."
        )
    return head + " Offline mode — canned/recorded replies, no model calls, nothing billed."


def _effective_model(provider: str, model: str | None) -> tuple[str | None, str]:
    """(model passed to the engine, human display) for the consent screen.

    A None model falls back to the provider's cheap default; the display names
    the model that actually bills rather than a misleading "(default)".
    """
    if model:
        return model, model
    default = PROVIDER_DEFAULT_MODEL.get(provider.lower())
    if default:
        return default, f"{default} (provider default)"
    return None, "(provider default)"


def _parse_judge_specs(
    judge: list[str] | None,
    judge_provider: str | None,
    judge_model: str | None,
    judge_models: list[str] | None,
    sut_provider: str,
    sut_model: str | None,
) -> list[tuple[str, str | None]]:
    """Resolve the judge set as (provider, model) pairs.

    A judge may be named by provider alone (`anthropic`) or as `provider:model`
    (`anthropic:claude-sonnet-4-6`); a bare provider resolves to that provider's
    default model downstream. Precedence: explicit `--judge` (repeatable, any
    provider) > `--judge-models` / `--judge-model` > `--judge-provider` alone >
    self-judge (the SUT's own provider+model). Two or more distinct pairs become
    an engine ensemble (a judge panel).
    """
    if judge:
        specs: list[tuple[str, str | None]] = []
        for item in judge:
            prov, _, mdl = item.partition(":")
            specs.append((prov.strip().lower(), mdl.strip() or None))
        return specs
    jprov = (judge_provider or sut_provider).lower()
    if judge_models:
        return [(jprov, m) for m in judge_models]
    if judge_model:
        return [(jprov, judge_model)]
    if judge_provider:
        return [(jprov, None)]  # provider-only judge → default model resolved downstream
    return [(sut_provider, sut_model)]  # self-judge


def _require_credential(provider: str) -> str:
    """The provider's API key from the environment, or a fail-fast SystemExit
    naming exactly which variable to set (and that it belongs in settings, not
    on the command line). Never prompts, so a headless run can't hang."""
    key = resolve_credential(provider, os.environ)
    if key:
        return key
    names = credential_env_vars(provider)
    if not names:
        raise SystemExit(
            f"Unknown provider '{provider}'. Valid: {', '.join(sorted(ACCOUNT_LABEL))}."
        )
    want = (" and " if credential_requires_all(provider) else " or ").join(names)
    raise SystemExit(
        f"Provider '{provider}' needs {want} set in the environment. Put it in "
        "your Claude Code settings.json \"env\" block (never on the command line "
        "or in chat), then re-run."
    )


def _preflight_provider(provider: str) -> None:
    """Confirm the provider's SDK is importable before a live run, so a missing
    extra fails at the consent screen with an install hint, not mid-run."""
    try:
        resolve_provider(provider)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


# --- Live-run model preflight ("canary") --------------------------------------
# A bad model id (a stale/typo'd slug, an unavailable endpoint) 404s on EVERY
# call, so without a preflight the whole run bills hundreds of probes against a
# model that never answers and then grades the empty/errored replies into a
# meaningless F. One tiny call per distinct billable model catches that at the
# consent boundary, before anything bills.

class _CanaryTarget(NamedTuple):
    label: str  # "agent under test" | "judge"
    provider: str
    model: str | None
    api_key: str
    endpoint: str | None


async def _canary_probe(target: _CanaryTarget) -> str | None:
    """One minimal call confirming (provider, model) resolves and the key
    authenticates. Returns None when the model is reachable, or a short error
    string for a deterministic failure (bad slug / key / endpoint) that would
    repeat on every billed call. Transient conditions (rate limit, timeout) and
    an empty-but-valid reply count as reachable — they don't prove the model is
    wrong, so they must not abort a legitimate run."""
    provider = cast(ChatProvider, resolve_provider(target.provider))
    config = ProviderConfig(
        provider=target.provider,
        api_key=target.api_key,
        model=target.model,
        endpoint=target.endpoint,
        max_tokens=16,
    )
    try:
        await provider.send_message(
            [ChatMessage(role="user", content="ping")], config
        )
        return None
    except ProviderEmptyContentError:
        return None  # reached the model; it returned nothing for a 1-token ping
    except (ProviderRateLimitError, ProviderTimeoutError):
        return None  # model exists; we're throttled/slow, not misconfigured
    except ProviderError as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        await provider.aclose()


def _canary_targets(
    sut_provider: str,
    sut_model: str | None,
    api_key: str,
    judge_specs: list[tuple[str, str | None]],
    judge_keys: dict[str, str],
    endpoint: str | None,
) -> list[_CanaryTarget]:
    """The distinct (provider, model) pairs a live run will bill — the agent
    under test and each judge — deduped so a self-judge probes once. The SUT
    carries --endpoint (e.g. azure); judges resolve their provider's default
    URL, matching how run_diagnostic_api wires them."""
    targets = [
        _CanaryTarget("agent under test", sut_provider, sut_model, api_key, endpoint)
    ]
    targets += [
        _CanaryTarget("judge", p, m, judge_keys.get(p, ""), None)
        for p, m in judge_specs
    ]
    seen: set[tuple[str, str | None, str | None]] = set()
    distinct: list[_CanaryTarget] = []
    for t in targets:
        key = (t.provider, t.model, t.endpoint)
        if key in seen:
            continue
        seen.add(key)
        distinct.append(t)
    return distinct


def _preflight_models(targets: list[_CanaryTarget]) -> None:
    """Probe every billable model once and abort the whole run if any can't be
    reached — BEFORE billing the real probes. A live run with a bad model id
    otherwise bills hundreds of 404s and grades the wreckage into a false F."""
    async def _gather() -> list[str | None]:
        return await asyncio.gather(*(_canary_probe(t) for t in targets))

    errors = asyncio.run(_gather())
    failures = [(t, err) for t, err in zip(targets, errors) if err]
    if not failures:
        return
    lines = "\n".join(
        f"  - {t.label} — {t.provider}:{t.model or '(default)'} — {err}"
        for t, err in failures
    )
    raise SystemExit(
        "Preflight failed — these model(s) could not be reached, so the run was "
        "ABORTED before billing any probes:\n"
        f"{lines}\n\n"
        "Check the model id, the API key, and the endpoint, then re-run. (A bad "
        "model id 404s on every call; without this preflight the whole run would "
        "bill against a model that never answers and grade the empty replies "
        "into a meaningless F.)\n"
        "If the model legitimately rejects tiny probes (e.g. a reasoning model "
        "that needs a large token budget), pass --no-preflight to skip this check."
    )


def _judge_relation(
    sut_provider: str, sut_model: str | None, specs: list[tuple[str, str | None]]
) -> str:
    """'self' (a judge is the same provider AND model as the SUT),
    'same-provider' (all judges share the SUT's provider/family), or
    'cross-vendor' (at least one judge is a different provider)."""
    sut = (sut_provider.lower(), sut_model)
    if any((p, m) == sut for p, m in specs):
        return "self"
    if all(p == sut_provider.lower() for p, _ in specs):
        return "same-provider"
    return "cross-vendor"


def _open_command(path: str) -> str:
    """A copy-pasteable command to open a written scorecard in the OS default
    app — macOS `open`, Windows `start`, otherwise `xdg-open`. The artifact/HTML
    is a standalone file (this terminal can't render it inline), so the run ends
    by telling the user how to look at it instead of leaving it unmentioned."""
    if sys.platform == "darwin":
        return f"open {path}"
    if sys.platform.startswith("win"):
        return f"start {path}"
    return f"xdg-open {path}"


def _honesty_note(relation: str) -> str:
    if relation == "self":
        return (
            "Note: a judge is the SAME provider and model as the agent under "
            "test — a self-diagnostic. A model grading itself flatters the "
            "result; treat this as a smoke test, not a certification."
        )
    if relation == "same-provider":
        return (
            "Note: the judge(s) share the agent's provider/family (independent "
            "of the exact model, but not a cross-vendor check)."
        )
    return (
        "Note: the judge(s) are a different provider from the agent under test — "
        "an independent, cross-vendor grade."
    )


def _judge_config_live(
    specs: list[tuple[str, str | None]], keys: dict[str, str]
) -> JudgeConfig:
    """Single judge, or ≥2 distinct (provider, model) judges → engine ensemble.

    Each judge carries its own provider, model, and env-resolved key, so a panel
    can mix vendors. Identical specs collapse to a single judge (the same model
    twice is not an ensemble)."""
    deduped = list(dict.fromkeys(specs))
    if len(deduped) >= 2:
        return JudgeConfig(
            providers=[
                JudgeProviderSpec(provider=p, model=m, api_key=keys.get(p, ""))
                for p, m in deduped
            ]
        )
    p, m = deduped[0]
    return JudgeConfig(provider=p, model=m, api_key=keys.get(p, ""))


def _judge_config_bridge(
    judge_model: str | None, judge_models: list[str] | None
) -> JudgeConfig:
    """Offline judge over the bridge seam: single, or ≥2 distinct models → an
    engine ensemble. The provider is always 'bridge' (canned/recorded replies)."""
    if judge_models and len(judge_models) >= 2:
        return JudgeConfig(
            providers=[JudgeProviderSpec(provider="bridge", model=m) for m in judge_models]
        )
    if judge_models:
        judge_model = judge_models[0]
    return JudgeConfig(provider="bridge", model=judge_model)


async def run_diagnostic(
    test_ids: list[str],
    fixture_path: Path,
    sut_transport: Transport,
    judge_transport: Transport,
    *,
    sut_model: str | None,
    judge_model: str | None,
    judge_models: list[str] | None = None,
    sut_provider: BridgeProvider | None = None,
    system_name: str = SYSTEM_NAME,
    progress_callback=None,
) -> TestRunResult:
    clear_transports()
    set_transport("sut", sut_transport)
    set_transport("judge", judge_transport)

    judge_config = _judge_config_bridge(judge_model, judge_models)
    fixture = load_fixture(fixture_path)
    try:
        return await run_selected(
            test_ids=set(test_ids),
            provider=sut_provider or BridgeProvider(channel="sut"),
            fixture=fixture,
            model=sut_model,
            run_nonce="0" * 16,  # pinned; replay keys also normalize nonces
            judge_config=judge_config,
            pipeline_config=_PIPELINE.model_copy(update={"sut_via_bridge": True}),
            system_name=system_name,
            system_version=SYSTEM_VERSION,
            progress_callback=progress_callback,
        )
    finally:
        await sut_transport.aclose()
        await judge_transport.aclose()


async def run_diagnostic_api(
    test_ids: list[str],
    fixture_path: Path,
    *,
    provider: str,
    api_key: str,
    sut_model: str | None,
    judge_config: JudgeConfig,
    endpoint: str | None = None,
    system_name: str = SYSTEM_NAME,
    progress_callback=None,
) -> TestRunResult:
    """`--mode api`: the agent under test and the judge(s) run on real provider
    APIs, billed to each provider's own account. The same engine path as
    `ifixai run --provider <provider>`, with a fresh per-run nonce like the
    classic CLI (no replay keying to preserve here)."""
    fixture = load_fixture(fixture_path)
    return await run_selected(
        test_ids=set(test_ids),
        provider=provider,
        api_key=api_key,
        fixture=fixture,
        model=sut_model,
        endpoint=endpoint,
        run_nonce=generate_run_nonce(),
        judge_config=judge_config,
        pipeline_config=_PIPELINE,
        system_name=system_name,
        system_version=SYSTEM_VERSION,
        progress_callback=progress_callback,
    )


def _coverage_rows(result: TestRunResult) -> list[tuple[str, str, str]]:
    """(pillar, score, 'N of M assessed') for pillars that ran anything."""
    rows = []
    for cs in result.category_scores:
        ran = len(cs.test_ids)
        if ran == 0:
            continue
        score = "—" if cs.score is None else f"{cs.score * 100:.0f}%"
        rows.append((cs.category.value.title(), score, f"{cs.test_count} of {ran} assessed"))
    return rows


def below_threshold_note(result: TestRunResult) -> str | None:
    """A grade is an aggregate, so individual inspections can fall below their
    own pass threshold while the letter still reads A. Surfacing that next to
    the grade keeps a high letter from being misread as 'everything passed' —
    the methodological-honesty commitment (plan §12). Returns None when every
    scored inspection cleared its threshold."""
    failed = [br for br in result.test_results if br.status == TestStatus.FAIL]
    if not failed:
        return None
    parts = ", ".join(
        f"{br.test_id} ({br.score * 100:.1f}% < {br.threshold:.0%})"
        for br in sorted(failed, key=lambda b: b.test_id)
    )
    return (
        f"⚠ {len(failed)} inspection(s) scored below their own pass threshold: "
        f"{parts}. The overall grade is a weighted aggregate — review these "
        "individually; a high letter does not mean every inspection passed."
    )


class _RunHealth(NamedTuple):
    n_inspections: int  # inspections that ran
    errored: int  # inspections that crashed before producing evidence (status ERROR)
    total: int  # evidence items across every inspection
    scorable: int  # produced a graded result (no extraction_error)
    unreachable: int  # a model call failed to communicate (agent OR judge)
    judge_broke: int  # the judge replied but the verdict was unusable

    @property
    def invalid(self) -> bool:
        """The run measured (almost) nothing — the letter grade rests on noise
        and must be read as a measurement failure, not a result. True when most
        inspections crashed before producing evidence, when every probe was
        unscorable, or when a model was unreachable on most probes."""
        if self.n_inspections and self.errored / self.n_inspections >= 0.5:
            return True  # most inspections crashed (no evidence to grade)
        if self.total == 0:
            return False  # nothing errored, but no gradeable probes either
        if self.scorable == 0:
            return True  # every probe was unscorable
        return self.unreachable / self.total >= 0.5

    @property
    def low_confidence(self) -> bool:
        """Not outright invalid, but under half the probes produced a graded
        reply — the grade is thin."""
        return self.total > 0 and not self.invalid and self.scorable / self.total < 0.5

    @property
    def judge_attempts(self) -> int:
        return self.scorable + self.judge_broke


def run_health(result: TestRunResult) -> _RunHealth:
    """Aggregate the failure signals the per-inspection scores hide. The engine
    already EXCLUDES an unscorable probe from a score (via its ``extraction_error``
    flag) and reports a crashed inspection as ERROR with no evidence, so a run
    where the model was unreachable, the judge kept breaking its verdict, or most
    inspections crashed can still print a confident letter from a handful of
    survivors. This counts those signals so the operator can flag a measurement
    failure (model unreachable / inspections crashed) or a weak grader (judge
    broke the contract) right next to the grade.

    COMMUNICATION is a failed model call on either seam (agent under test or
    judge); CONTRACT/EXTRACTION mean the judge answered but the verdict was
    unusable — unambiguously a grader-health problem."""
    errored = total = scorable = unreachable = judge_broke = 0
    for br in result.test_results:
        if br.status == TestStatus.ERROR:
            errored += 1
        for ev in br.evidence:
            total += 1
            kind = getattr(ev, "extraction_error", None)
            if kind is None:
                scorable += 1
            elif kind == JudgeErrorKind.COMMUNICATION:
                unreachable += 1
            elif kind in (JudgeErrorKind.CONTRACT, JudgeErrorKind.EXTRACTION):
                judge_broke += 1
            else:
                unreachable += 1
    return _RunHealth(len(result.test_results), errored, total, scorable, unreachable, judge_broke)


def measurement_failure_banner(health: _RunHealth) -> str | None:
    """A loud, grade-suppressing banner when the run did not actually measure the
    agent. Names the dominant cause so the remediation is right — a dead model
    points at the model id/key/endpoint, a broken grader points at the judge.
    Returns None for a healthy run."""
    if not health.invalid:
        return None
    if health.scorable == 0 and health.judge_broke and health.unreachable == 0 and not health.errored:
        # Every probe reached a model but the judge produced no usable verdict —
        # a broken grader, not a model problem. Don't send the user to the model.
        cause = (
            f"the judge returned {health.judge_broke} unusable verdict(s) and not "
            "one produced a graded reply — a broken grader, not a model problem. "
            "Switch to a more reliable judge and re-run."
        )
    elif health.errored and health.errored >= health.unreachable:
        cause = (
            f"{health.errored} of {health.n_inspections} inspections crashed before "
            "producing any evidence (the agent under test or its configuration "
            "errored out). Check the model id / key / endpoint and re-run."
        )
    else:
        cause = (
            f"{health.unreachable} of {health.total} model calls failed to complete "
            "(the agent under test or a judge was unreachable). Check the model id / "
            "key / endpoint (the preflight catches a bad model id before billing) "
            "and re-run."
        )
    return (
        "*** RUN INVALID — measurement failure, not a result. ***\n"
        f"  {cause} The grade below is computed from almost no evidence — ignore "
        "the letter."
    )


def judge_health_note(health: _RunHealth) -> str | None:
    """A note when the judge broke its verdict contract often enough to make the
    grade untrustworthy. The broken probes were excluded from scoring, but an
    inspection that leaned on a flaky grader is not a reliable finding. Returns
    None when the judge held its contract."""
    if health.judge_broke == 0:
        return None
    return (
        f"⚠ Judge health: the grader returned {health.judge_broke} unusable "
        f"verdict(s) (broke the scoring contract) of {health.judge_attempts} "
        "grading attempts. Those probes were dropped, so inspections that leaned "
        "on a flaky grader are weak signal — prefer a stronger or independent "
        "judge for a result you can trust."
    )


def render_html(
    result: TestRunResult,
    *,
    live: bool,
    transport: str,
    sut_model,
    judge_model,
    honesty_note: str,
) -> str:
    """HTML scorecard that mirrors the CLI report — the same sections (header,
    Overall Score, Category Scores, Mandatory Minimums, Test Results) with the
    same columns and values (it reuses the CLI's own formatters) — plus the
    plugin's coverage column and honesty notes (§11/§12). This is the static
    fallback report; the interactive view is render_artifact."""
    e = html.escape

    def pct(s):
        return "n/a" if s is None else f"{s * 100:.1f}%"

    def kv(rows):
        body = "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in rows)
        return f"<table class='kv'><tbody>{body}</tbody></table>"

    # Header meta — matches render_header.
    eval_mode = "deterministic"
    for br in result.test_results:
        if br.evaluation_mode:
            eval_mode = br.evaluation_mode.value
            break
    meta = kv(
        [
            ("Provider", result.provider),
            ("Fixture", result.fixture_name),
            ("Evaluation Date", result.evaluation_date.strftime("%Y-%m-%d %H:%M UTC")),
            ("Run Mode", result.run_mode),
            ("Evaluation Mode", eval_mode),
            ("Transport", transport),
            ("SUT model", sut_model or "(default)"),
            ("Judge model", judge_model or "(default)"),
            ("Spec Version", result.specification_version),
        ]
    )

    # Overall Score — matches render_summary.
    overall_display = (
        "n/a (insufficient evidence)"
        if result.overall_score is None
        else pct(result.overall_score)
    )
    if result.overall_score_before_cap is not None:
        overall_display += f" (capped from {pct(result.overall_score_before_cap)})"
    overall = kv(
        [
            ("Overall Score", overall_display),
            ("Grade", result.grade.value),
            ("Verdict", _format_run_verdict(result)),
            ("Strategic Score", f"{result.strategic_score:.1%}"),
            ("Mandatory Minimums", _format_run_minimums_status(result)),
        ]
    )

    # Category Scores — matches render_category_table, plus a coverage column.
    cat_rows = ""
    for cs in result.category_scores:
        ran = len(cs.test_ids)
        coverage = f"{cs.test_count} of {ran} assessed" if ran else "—"
        cat_rows += (
            f"<tr><td>{e(cs.category.value)}</td><td>{e(pct(cs.score))}</td>"
            f"<td>{ran}</td><td>{e(coverage)}</td></tr>"
        )

    # Mandatory Minimums — matches render_mandatory_minimums.
    mm_rows = "".join(
        f"<tr><td>{e(tid)}</td><td class='s-{v.value}'>{e(v.value.upper())}</td></tr>"
        for tid, v in sorted(result.mandatory_minimum_status.items())
    )

    # Test Results — matches render_test_table columns + values.
    unscored = {TestStatus.INCONCLUSIVE, TestStatus.ERROR}
    test_rows = ""
    for br in sorted(result.test_results, key=lambda r: r.test_id):
        score_display = "n/a" if br.status in unscored else f"{br.score:.1%}"
        if br.confidence_interval and br.status not in unscored:
            ci = br.confidence_interval
            score_display += f" [{ci.lower:.2f}, {ci.upper:.2f}]"
        test_rows += (
            f"<tr><td>{e(br.test_id)}</td><td>{e(br.name)}</td>"
            f"<td>{e(score_display)}</td><td>{br.threshold:.0%}</td>"
            f"<td>{e(_dominant_evaluation_path(br))}</td><td>{e(_format_method_mix(br))}</td>"
            f"<td class='s-{br.status.value}'>{e(br.status.value.upper())}</td></tr>"
        )

    grade_class = _GRADE_CLASS.get(result.grade.value, "inconclusive")
    stability = grade_stability(result.overall_score)
    threshold_note = below_threshold_note(result)
    threshold_banner = (
        f"<div class='label' style='border-left-color:var(--warn);color:var(--warn)'>"
        f"{e(threshold_note)}</div>"
        if threshold_note
        else ""
    )
    offline_banner = (
        ""
        if live
        else (
            "<div class='label' style='border-left-color:var(--bad);color:var(--bad);"
            "font-weight:600'>OFFLINE RUN — this scorecard was produced in "
            f"'{e(transport)}' mode from canned/recorded replies. It rehearses the "
            "pipeline and is NOT a diagnostic of a real agent.</div>"
        )
    )
    # On a live run the SUT is a bare model API call with no tools or connectors
    # attached, so probe text can't trigger a real action — describe THAT, not a
    # sandbox (the plugin no longer shells out to a local CLI).
    containment = (
        " Live runs call the model's API directly with no tools, connectors, or "
        "file access attached, so the agent may echo tool-call syntax in its "
        "reply text but nothing executes. Still, run adversarial probes only "
        "against a throwaway account with no real secrets."
        if live
        else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>iFixAi Scorecard — {e(result.system_name)}{f" v{e(result.system_version)}" if result.system_version else ""}</title>
<style>
 :root{{--line:#e5e7eb;--ink:#111827;--dim:#6b7280;--good:#15803d;--bad:#b91c1c;--warn:#b45309}}
 body{{font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:var(--ink)}}
 h1{{margin:0 0 .25rem;font-size:1.5rem}} .sub{{color:var(--dim);font-weight:400;font-size:1rem}}
 h2{{margin:1.8rem 0 .4rem;font-size:1.05rem;border-bottom:2px solid var(--line);padding-bottom:.2rem}}
 .gradebox{{display:flex;align-items:center;gap:1rem;margin:1rem 0}}
 .grade{{font-size:3.2rem;font-weight:800;line-height:1}}
 table{{border-collapse:collapse;width:100%;margin:.4rem 0;font-size:.92rem}}
 th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
 thead th{{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;color:var(--dim)}}
 table.kv th{{width:11rem;color:var(--dim);font-weight:600;text-transform:none;font-size:.92rem}}
 .s-pass{{color:var(--good);font-weight:600}} .s-fail{{color:var(--bad);font-weight:700}}
 .s-inconclusive{{color:var(--warn)}} .s-error{{color:var(--bad);font-weight:700}}
 .label{{background:#f9fafb;border-left:3px solid #9ca3af;padding:.7rem 1rem;margin:1.5rem 0;font-size:.88rem;color:#374151}}
</style></head><body>
<h1>iFixAi Scorecard — {e(result.system_name)}{f' <span class="sub">v{e(result.system_version)}</span>' if result.system_version else ''}</h1>
{offline_banner}
<div class="gradebox"><div class="grade s-{grade_class}">{e(result.grade.value)}</div>
<div>Overall <strong>{e(overall_display)}</strong><br><span class="sub">{e(stability['note'])}</span></div></div>
{threshold_banner}
{meta}
<h2>Overall Score</h2>
{overall}
<h2>Category Scores</h2>
<table><thead><tr><th>Category</th><th>Score</th><th>Tests</th><th>Coverage</th></tr></thead><tbody>{cat_rows}</tbody></table>
<h2>Mandatory Minimums</h2>
<table><thead><tr><th>Test</th><th>Status</th></tr></thead><tbody>{mm_rows}</tbody></table>
<h2>Test Results</h2>
<table><thead><tr><th>ID</th><th>Name</th><th>Score</th><th>Threshold</th><th>Path</th><th>Method</th><th>Status</th></tr></thead><tbody>{test_rows}</tbody></table>
<div class="label"><strong>Honesty labels.</strong> {e(honesty_note)} SUT boundary: model +
CLAUDE.md + tools (not the full harness/permission layer). Coverage is qualified per
pillar; a thin pillar is not a clean pass. Verdicts are sampled.{containment}</div>
</body></html>"""


def _make_transports(args):
    if args.mode == "stub":
        return ConstantTransport(CANNED_SUT_REPLY), StubJudgeTransport(passed=True), None
    if args.mode == "record":
        store: dict = {}
        return (
            RecordingTransport(ConstantTransport(CANNED_SUT_REPLY), store),
            RecordingTransport(StubJudgeTransport(passed=True), store),
            store,
        )
    if args.mode == "replay":
        store = load_recording(args.recording or GOLDEN_RECORDING)
        return ReplayTransport(store), ReplayTransport(store), None
    raise SystemExit(f"unknown mode: {args.mode}")


def _print_progress(test_id: str, index: int, total: int, result) -> None:
    """Stream one line per finished inspection (live runs take minutes each)."""
    print(f"  [{index}/{total}] {test_id}: {result.status.value.upper()}", flush=True)


def main() -> None:
    # The consent screen and progress lines print non-ASCII glyphs (⚠, →). On
    # native Windows the console/pipe defaults to the legacy code page (cp1252),
    # which can't encode them, so a bare print would raise UnicodeEncodeError and
    # abort before the run. Force UTF-8 on our streams; no-op where already UTF-8.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="iFixAi Phase 2 multi-inspection orchestrator.")
    parser.add_argument(
        "--mode", choices=["stub", "record", "replay", "api"], default="stub",
        help="stub/record/replay rehearse offline; 'api' is the live run on real "
        "provider APIs (billed to each provider's account).",
    )
    parser.add_argument(
        "--provider", default=DEFAULT_PROVIDER,
        help="provider for the agent under test on --mode api (anthropic, openai, "
        "gemini, azure, bedrock, openrouter, …). Its key is read from the environment.",
    )
    parser.add_argument(
        "--endpoint", default=None,
        help="custom base URL / Azure endpoint for the agent-under-test provider, "
        "when it needs one",
    )
    parser.add_argument(
        "--recording",
        default=None,
        help="recording file (replay reads the committed golden by default; "
        "record requires an explicit new path)",
    )
    parser.add_argument(
        "--tests",
        default=None,
        help="comma-separated test ids (default: the standard set; in replay "
        "mode, the set the golden recording covers)",
    )
    parser.add_argument("--fixture", default=str(FIXTURE_PATH))
    parser.add_argument(
        "--profile",
        default=None,
        help="usage-profile JSON from discovery; shows the confirm summary, "
        "generates the fixture, and overrides --fixture",
    )
    parser.add_argument(
        "--fixture-out",
        default=None,
        help="where to write the profile-derived fixture for review/editing "
        "(default: ifixai-fixture.yaml beside the profile); the run uses this file",
    )
    parser.add_argument("--sut-model", default=None)
    parser.add_argument(
        "--judge",
        action="append",
        default=None,
        metavar="PROVIDER[:MODEL]",
        help="a judge as a provider (e.g. anthropic) or provider:model; a bare "
        "provider uses that provider's default model. Repeat for a panel (two or "
        "more distinct judges → ensemble). Overrides --judge-provider/-model.",
    )
    parser.add_argument(
        "--judge-provider",
        default=None,
        help="provider for the single judge when --judge is not used "
        "(default: the agent-under-test provider → self-judge)",
    )
    parser.add_argument("--judge-model", default=None)
    parser.add_argument(
        "--judge-models",
        default=None,
        help="comma-separated judge models on --judge-provider; two or more → "
        "ensemble (a same-provider panel)",
    )
    parser.add_argument("--html-out", default=None, help="write the static HTML scorecard here")
    parser.add_argument(
        "--artifact-out", default=None,
        help="write the self-contained interactive HTML artifact here "
        "(searchable/filterable checks, evidence, and a diff vs --prev-json)",
    )
    parser.add_argument("--json-out", default=None, help="write the full results JSON here (the CI source of truth)")
    parser.add_argument(
        "--prev-json", default=None,
        help="a previous run's --json-out; the artifact shows what changed since it",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="checkpoint file for resume after an interruption on a bridge mode "
        "(cleared on success)",
    )
    parser.add_argument(
        "--settings",
        default=None,
        help="Claude Code settings.json — grades the deterministic control "
        "plane (adds B02 to the set)",
    )
    parser.add_argument(
        "--preset",
        choices=["quick", "standard", "full"],
        default=None,
        help="named run size; overrides --tests and shows a cost estimate first",
    )
    parser.add_argument(
        "--yes", action="store_true", help="confirm and run after the cost estimate"
    )
    parser.add_argument(
        "--no-preflight", action="store_true",
        help="skip the one-call-per-model preflight that aborts a live run on a "
        "bad model id before billing (escape hatch for a provider that rejects "
        "tiny probes)",
    )
    parser.add_argument(
        "--no-telemetry", action="store_true",
        help="disable pseudonymous run telemetry for this run (IFIXAI_TELEMETRY=0 "
        "or DO_NOT_TRACK=1 disables it permanently)",
    )
    args = parser.parse_args()

    if args.no_telemetry:
        telemetry.disable()

    live = args.mode in LIVE_MODES
    sut_provider = args.provider.lower()

    # Resolve judge models once, before consent, so the consent line reflects
    # what actually runs (deduped: the same model twice is not an ensemble).
    judge_models = (
        [m.strip() for m in args.judge_models.split(",") if m.strip()]
        if args.judge_models
        else None
    )
    if judge_models:
        judge_models = list(dict.fromkeys(judge_models))

    # --- Live (API-key) setup: resolve models, judges, SDKs, and keys up front
    # so a missing credential or SDK fails fast with a named fix, never mid-run
    # and never on a prompt. ---
    sut_model_eff = args.sut_model
    sut_model_display = args.sut_model or "(default)"
    judge_specs: list[tuple[str, str | None]] = []
    judge_keys: dict[str, str] = {}
    api_key = ""
    honesty_note = ""
    relation = ""
    if live:
        if sut_provider in MODEL_REQUIRED_PROVIDERS and not args.sut_model:
            raise SystemExit(
                f"--provider {sut_provider} needs an explicit --sut-model "
                "(a deployment name or model id); it has no safe default."
            )
        sut_model_eff, sut_model_display = _effective_model(sut_provider, args.sut_model)

        judge_specs = _parse_judge_specs(
            args.judge, args.judge_provider, args.judge_model, judge_models,
            sut_provider, sut_model_eff,
        )
        # Resolve a None judge model to that provider's default for billing
        # honesty and so the engine gets a concrete model.
        judge_specs = [
            (p, _effective_model(p, m)[0] if m is None else m) for p, m in judge_specs
        ]
        for p, m in judge_specs:
            if p in MODEL_REQUIRED_PROVIDERS and not m:
                raise SystemExit(
                    f"judge provider {p} needs an explicit model "
                    "(provider:model); it has no safe default."
                )

        # SDKs present? (consent-screen failure with an install hint, not mid-run)
        for p in dict.fromkeys([sut_provider] + [jp for jp, _ in judge_specs]):
            _preflight_provider(p)

        # Keys present? (env-only; one key covers a provider used for both roles)
        api_key = _require_credential(sut_provider)
        for p in dict.fromkeys([jp for jp, _ in judge_specs]):
            judge_keys[p] = api_key if p == sut_provider else _require_credential(p)

        relation = _judge_relation(sut_provider, sut_model_eff, judge_specs)
        honesty_note = _honesty_note(relation)

        if args.checkpoint:
            raise SystemExit(
                "--checkpoint applies to the bridge modes; --mode api has no "
                "transport seam to checkpoint (an interrupted api run starts over)."
            )
        if args.settings:
            raise SystemExit(
                "--settings wraps the bridge SUT; use a bridge mode "
                "(stub/record/replay), not --mode api."
            )
    else:
        honesty_note = (
            "Offline rehearsal — canned/recorded replies, not a diagnostic of a real agent."
        )

    if args.mode == "record" and not args.recording:
        raise SystemExit(
            "record mode needs an explicit --recording <new file> — the "
            "committed golden anchor is never overwritten by default."
        )

    # Replay verdicts are keyed on (channel, model, messages): a model flag
    # changes the key and misses every recorded reply. Refuse up front rather
    # than silently grade an all-ERROR run.
    if args.mode == "replay" and (args.sut_model or args.judge or args.judge_model or args.judge_models):
        raise SystemExit(
            "replay uses the models the recording was made with — drop "
            "--sut-model/--judge(-model[s]), or record a fresh recording."
        )

    profile = None
    if args.profile:
        try:
            profile = load_profile(args.profile)
        except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
            raise SystemExit(f"--profile {args.profile}: {exc}") from None
    # A recording stays keyed to the committed fixture; a --profile fixture is
    # generated fresh, so record/replay still want --fixture, not --profile.
    if profile is not None and args.mode in {"replay", "record"}:
        raise SystemExit(
            "recordings are keyed to a committed fixture; a --profile fixture is "
            f"generated fresh. Use --fixture for {args.mode}, not --profile."
        )
    profile_fixture_path = None
    if profile is not None:
        print(describe_profile(profile))
        # Discovery reads untrusted files and is only type-checked; surface any
        # suspect mappings (a downgraded/injected label, a gutted evidence floor)
        # on the confirm screen so the user can correct before spending anything.
        for w in profile_warnings(profile):
            print(f"  ⚠ {w}")
        # Build the FULL fixture now and persist it to a visible file the run uses
        # verbatim — every role, grant, and policy that gets graded is on screen
        # and on disk to edit. "What you see is what runs."
        fixture_dict = build_fixture_dict(profile)
        profile_fixture_path = (
            Path(args.fixture_out) if args.fixture_out
            else Path(args.profile).with_name("ifixai-fixture.yaml")
        )
        write_fixture_dict(fixture_dict, profile_fixture_path)
        print(describe_fixture(fixture_dict, synthetic=True))
        print(
            f"  Fixture written to {profile_fixture_path} — edit it (or the profile) "
            "to alter roles, permissions, or policies; the run uses exactly this file.\n"
        )
        for w in fixture_floor_warnings(fixture_dict):
            print(f"  ⚠ {w}")

    if args.preset:
        test_ids = list(PRESETS[args.preset]["tests"])
    elif args.tests:
        test_ids = [t.strip().upper() for t in args.tests.split(",") if t.strip()]
    else:
        test_ids = list(GOLDEN_SET) if args.mode == "replay" else list(DEFAULT_SET)

    unknown = sorted(set(test_ids) - set(SPEC_BY_ID))
    if unknown:
        raise SystemExit(
            f"unknown test ids: {', '.join(unknown)} — valid ids: "
            + ", ".join(sorted(SPEC_BY_ID))
        )

    # The golden recording only covers GOLDEN_SET; a replay over anything else
    # would miss → per-inspection ERROR → a misleading clean grade. Refuse.
    if args.mode == "replay" and not args.recording and set(test_ids) - set(GOLDEN_SET):
        raise SystemExit(
            f"the committed golden recording covers {', '.join(GOLDEN_SET)}; "
            "pass --tests within that set, or --recording for a wider one."
        )

    sut_provider_obj = None
    if args.settings:
        try:
            settings = json.loads(Path(args.settings).read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise SystemExit(f"--settings {args.settings}: {exc}") from None
        sut_provider_obj = ClaudeCodeGovernanceProvider(settings)
        if "B02" not in test_ids:
            test_ids.append("B02")
            print("settings.json provided — added B02 (deterministic control plane) to the set.")

    # Effective models for honest reporting: live resolves above; offline
    # transports ignore models.
    if live:
        eff_sut = sut_model_display
        eff_judge = ", ".join(
            f"{p}:{m}" if p != sut_provider else (m or "(default)")
            for p, m in judge_specs
        )
    else:
        eff_sut = args.sut_model or "(canned)"
        eff_judge = ", ".join(judge_models) if judge_models else (args.judge_model or "(canned)")

    # A set missing a mandatory minimum is run as-is, but the gate then caps the
    # grade at D — warn rather than emit a confident, unexplained D.
    missing_mm = [m for m in MANDATORY_MINIMUMS if m not in test_ids]
    if missing_mm:
        print(
            f"⚠ Selected set omits mandatory minimum(s) {', '.join(missing_mm)} — "
            "the gate treats an absent minimum as unmet and caps the grade at D. "
            "Add them (or use --preset) for a grade above D."
        )

    # For an explicit (or hand-edited) fixture, show the same full breakdown
    # before a billable/preset confirm — what you see is what gets graded. The
    # profile path already printed it above.
    if profile is None and (live or args.preset):
        try:
            print(describe_fixture(read_fixture_dict(Path(args.fixture)), synthetic=False))
        except (OSError, ValueError, KeyError, TypeError):
            pass  # a describe failure must never block the run

    # Consent: a live (billable) run never starts without --yes; offline runs
    # only gate when a preset asked for the confirm screen.
    if live:
        print(f"Models — agent under test: {eff_sut}, judge: {eff_judge}.")
        # Always show the call-count/time estimate before a billable run, with or
        # without a preset — the inspection count alone undersells the real cost.
        print(cost_preview(args.preset, live=True, provider=sut_provider, n_tests=len(test_ids)))
        # Always name the account-level risk before a billable adversarial run,
        # not just on preset runs — price alone doesn't capture it.
        print(adversarial_account_risk(sut_provider))
        # A model grading itself is biased toward a pass — name it before spend.
        if relation == "self":
            print(
                f"⚠ A judge uses the SAME provider and model as the agent under "
                f"test ({sut_provider}:{sut_model_eff}). A model grading itself is "
                "biased toward passing — prefer an independent judge "
                "(--judge <other-provider>:<model>)."
            )
    elif args.preset:
        print(cost_preview(args.preset, live=False, provider=sut_provider, n_tests=len(test_ids)))
    if not args.yes and (live or args.preset):
        print("\nRe-run with --yes to start.")
        return

    # Offline rehearsals (stub/record/replay) are not real runs — only live runs
    # emit, so the metric counts diagnostics, not demos.
    if live:
        telemetry.show_disclosure()
        telemetry.emit_started("plugin")

    # The profile-derived fixture was already written to a visible file on the
    # confirm screen (above); run from exactly that file so what the user saw and
    # could edit is what gets graded. No throwaway temp, nothing deleted.
    if profile is not None:
        assert profile_fixture_path is not None  # written on the confirm screen above
        fixture_path = profile_fixture_path
    else:
        fixture_path = Path(args.fixture)

    # Title the scorecard with the agent's purpose (when profiled) instead of a
    # generic placeholder — the report should name the thing under test.
    system_name = profile.purpose if profile is not None else SYSTEM_NAME
    if len(system_name) > 80:
        system_name = system_name[:79] + "…"

    if live:
        # Preflight: one cheap call per distinct billable model. A bad model id
        # aborts here, before the run bills hundreds of probes that all 404.
        if not args.no_preflight:
            targets = _canary_targets(
                sut_provider, sut_model_eff, api_key, judge_specs, judge_keys, args.endpoint
            )
            print(
                f"Preflighting {len(targets)} model(s) (one cheap call each) before "
                "billing the run…",
                flush=True,
            )
            _preflight_models(targets)
        judge_config = _judge_config_live(judge_specs, judge_keys)
        result = asyncio.run(
            run_diagnostic_api(
                test_ids,
                fixture_path,
                provider=sut_provider,
                api_key=api_key,
                sut_model=sut_model_eff,
                judge_config=judge_config,
                endpoint=args.endpoint,
                system_name=system_name,
                progress_callback=_print_progress,
            )
        )
    else:
        sut_t, judge_t, store = _make_transports(args)

        # Checkpoint/resume: cache every reply as it lands, so a restart
        # serves finished calls from disk instead of re-running them.
        cp_path = Path(args.checkpoint) if args.checkpoint else None
        if cp_path is not None:
            cp_store = {}
            if cp_path.exists():
                try:
                    cp_store = load_recording(cp_path)
                except (OSError, ValueError):
                    print(f"checkpoint {cp_path} is unreadable — starting fresh.")
                    cp_store = {}
            if cp_store:
                print(f"resuming from checkpoint {cp_path} ({len(cp_store)} cached replies)")

            def _persist(s: dict) -> None:
                save_recording(s, cp_path)

            sut_t = CachingTransport(sut_t, cp_store, on_record=_persist)
            judge_t = CachingTransport(judge_t, cp_store, on_record=_persist)

        result = asyncio.run(
            run_diagnostic(
                test_ids,
                fixture_path,
                sut_t,
                judge_t,
                sut_model=args.sut_model,
                judge_model=args.judge_model,
                judge_models=judge_models,
                sut_provider=sut_provider_obj,
                system_name=system_name,
                progress_callback=_print_progress,
            )
        )
        if args.mode == "record" and store is not None:
            Path(args.recording).parent.mkdir(parents=True, exist_ok=True)
            save_recording(store, args.recording)
            print(f"recorded {len(store)} bridge replies → {args.recording}\n")
        # A finished run's checkpoint must not feed a future fresh run.
        if cp_path is not None and cp_path.exists():
            cp_path.unlink()
            print(f"checkpoint cleared (run completed): {cp_path}")

    # Carry the judge-independence signal into the results JSON (the CI source
    # of truth), so a self-graded run is as honest there as in the artifact.
    health: _RunHealth | None = None
    if live:
        result.self_judged = relation == "self"
        result.judge_relation = relation
        # Surface measurement failure (model unreachable) and grader fragility
        # (judge broke the contract) in the JSON too, so CI sees what the
        # console banners say — a confident-looking letter atop dead evidence
        # must not read as clean in automation.
        health = run_health(result)
        if health.invalid:
            result.validation_warnings.append(
                "run_invalid: measurement failure — "
                f"errored={health.errored}/{health.n_inspections} inspections, "
                f"unreachable={health.unreachable}/{health.total} model calls, "
                f"judge_broke={health.judge_broke}, scorable={health.scorable}. "
                "Ignore the grade."
            )
        if health.judge_broke:
            result.validation_warnings.append(
                f"judge_health: {health.judge_broke}/{health.judge_attempts} grading "
                "attempts broke the verdict contract (excluded from scoring)."
            )

    if not live:
        print(
            f"\n*** OFFLINE RUN — mode '{args.mode}' uses canned/recorded replies. "
            "This rehearses the pipeline; it is NOT a diagnostic of a real agent. ***\n"
        )
    print(generate_markdown_report(result))
    if live:
        telemetry.emit_completed("plugin")
    print("\n--- coverage ---")
    for pillar, score, cov in _coverage_rows(result):
        print(f"  {pillar:18} {score:>5}   {cov}")
    # A run that measured (almost) nothing must say so LOUDLY, before the letter —
    # otherwise a false F/0% computed from 404s reads as a real failing grade.
    if health is not None:
        banner = measurement_failure_banner(health)
        if banner:
            print(f"\n{banner}")
        elif health.low_confidence:
            print(
                f"\n  ⚠ Low-confidence run — only {health.scorable} of {health.total} "
                "probes produced a graded reply; the rest were unscorable. Read the "
                "grade cautiously."
            )
    overall_pct = "n/a" if result.overall_score is None else f"{result.overall_score * 100:.1f}%"
    print(f"\nOverall: {overall_pct}   Grade: {result.grade.value}   [transport: {args.mode}]")
    print(f"  {grade_stability(result.overall_score)['note']}")
    # A capped grade (mandatory minimum unmet) must say so — the letter alone
    # would read as a behavioral failure rather than a gate effect.
    if result.overall_score_before_cap is not None:
        print(
            f"  ⚠ Grade capped from {result.overall_score_before_cap * 100:.1f}% by the "
            "mandatory-minimum gate (a required inspection was unmet/absent), not by "
            "the behavioral scores."
        )
    threshold_note = below_threshold_note(result)
    if threshold_note:
        print(f"  {threshold_note}")
    errored = [br.test_id for br in result.test_results if br.status == TestStatus.ERROR]
    if errored:
        print(
            f"  ⚠ {len(errored)} inspection(s) errored and were excluded from scoring "
            f"({', '.join(sorted(errored))}); the grade reflects only what ran."
        )
    # A weak/flaky judge contaminates trust even when the run is otherwise valid —
    # the broken verdicts were dropped, but the inspections that leaned on them
    # are not believable findings. Name it next to the grade.
    if health is not None:
        jh_note = judge_health_note(health)
        if jh_note:
            print(f"  {jh_note}")
    # INCONCLUSIVE inspections aren't in the grade either, so a high letter can sit
    # atop thin coverage. Surface them next to the grade (symmetric with ERROR), and
    # call out a behavioral mandatory minimum here — that signals a fixture without
    # the evidence to grade it (e.g. B08 after a hollowed-out discovery), distinct
    # from a genuinely not-applicable minimum (e.g. P01 with no destructive capability).
    inconclusive = [br.test_id for br in result.test_results if br.status == TestStatus.INCONCLUSIVE]
    if inconclusive:
        msg = (
            f"  ⚠ {len(inconclusive)} inspection(s) were INCONCLUSIVE (insufficient "
            f"evidence — not counted in the grade): {', '.join(sorted(inconclusive))}. "
            "A high letter does not reflect these — check coverage."
        )
        mm_incon = sorted(set(inconclusive) & set(MANDATORY_MINIMUMS) - {"B01", "P01"})
        if mm_incon:
            msg += (
                f" Note {', '.join(mm_incon)} is a behavioral mandatory minimum with "
                "no gradeable evidence — your fixture may be too thin (or discovery was "
                "hollowed out); treat this run as low-confidence."
            )
        print(msg)
    if live:
        print(f"  {honesty_note}")

    if args.json_out:
        Path(args.json_out).write_text(generate_json_report(result), "utf-8")
        print(f"Results JSON → {args.json_out}")
    if args.html_out:
        Path(args.html_out).write_text(
            render_html(
                result, live=live, transport=args.mode,
                sut_model=eff_sut, judge_model=eff_judge, honesty_note=honesty_note,
            ),
            "utf-8",
        )
        print(f"HTML scorecard → {args.html_out}")
    if args.artifact_out:
        previous = None
        if args.prev_json:
            try:
                previous = json.loads(Path(args.prev_json).read_text("utf-8"))
            except (OSError, ValueError) as exc:
                print(f"  (skipping diff: --prev-json {args.prev_json} unreadable: {exc})")
        Path(args.artifact_out).write_text(
            render_artifact(
                result, live=live, transport=args.mode,
                sut_model=eff_sut, judge_model=eff_judge,
                honesty_note=honesty_note, previous=previous,
            ),
            "utf-8",
        )
        print(f"Interactive artifact → {args.artifact_out}")

    # The artifact/HTML is a standalone file this terminal can't render inline,
    # so end by pointing at the richest view written and how to open it.
    to_open = args.artifact_out or args.html_out
    if to_open:
        print(f"\nView the scorecard in your browser:\n  {_open_command(to_open)}")


if __name__ == "__main__":
    main()
