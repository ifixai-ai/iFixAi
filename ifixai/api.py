import logging

from ifixai._version import VERSION as __version__  # noqa: F401
from ifixai.harness.registry import ALL_SPECS
from ifixai.core.concurrency import ConcurrencyGovernor
from ifixai.core.fixture_loader import list_fixture_names, load_fixture
from ifixai.inspections.holdout_ids import generate_holdout_ids
from ifixai.judge.config import JudgeConfig
from ifixai.providers.base import ChatProvider
from ifixai.providers.resolver import resolve_provider, wrap_with_governance
from ifixai.reporting.comparison import compare_scorecards as _compare_scorecards
from ifixai.core.runner import (
    run_all,
    run_selected as _run_selected,
    run_single as _run_single,
    run_strategic as _run_strategic,
)
from ifixai.core.types import (
    TestResult,
    InspectionSpec,
    ComparisonReport,
    EvaluationPipelineConfig,
    Fixture,
    ProviderConfig,
    TestRunResult,
)

_logger = logging.getLogger(__name__)


def _resolve_fixture(fixture: str | Fixture) -> Fixture:
    if isinstance(fixture, Fixture):
        return fixture
    return load_fixture(fixture)


def _resolve_provider_with_governance(
    provider: str | ChatProvider,
    fixture_obj: Fixture,
) -> ChatProvider:
    """Resolve a provider and compose the fixture's governance bundle on it.

    Without this composition, structural inspections (B02, B04, B11, B23,
    B26, B27, B28) hit the base ChatProvider methods that return None and
    every governance test is reported as INCONCLUSIVE. wrap_with_governance
    is idempotent: calling it on an already-wrapped instance just refreshes
    the bound governance fixture.
    """
    provider_obj = resolve_provider(provider)
    if fixture_obj.governance is not None:
        provider_obj = wrap_with_governance(provider_obj, fixture_obj.governance)
    return provider_obj


def _build_config(
    provider: str | ChatProvider,
    api_key: str,
    endpoint: str | None,
    model: str | None,
    system_prompt: str | None,
    timeout: int,
    max_retries: int,
    temperature: float = 0.0,
    seed: int | None = None,
    run_nonce: str | None = None,
    holdout_ids: dict[str, str] | None = None,
) -> ProviderConfig:
    # holdout_ids must be populated for B01/B04/B16 to run; callers (CLI) can
    # supply their own (e.g. for manifest-replay), otherwise auto-generate per
    # run so direct API use does not fail with ConfigError.
    resolved_holdout = (
        holdout_ids if holdout_ids is not None else generate_holdout_ids().to_dict()
    )
    return ProviderConfig(
        provider=provider if isinstance(provider, str) else "custom",
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        timeout=timeout,
        max_retries=max_retries,
        temperature=temperature,
        seed=seed,
        run_nonce=run_nonce,
        holdout_ids=resolved_holdout,
    )


async def _aclose_provider(provider_obj: ChatProvider) -> None:
    """Best-effort teardown for the SUT provider's shared HTTP/SDK pool.

    Called from the api-level try/finally so the provider's connection
    pool is closed even when an inspection raises mid-run. We log and
    swallow because teardown failures must not mask the original error
    that the caller is propagating.
    """
    try:
        await provider_obj.aclose()
    except Exception:
        _logger.exception("Provider teardown failed during aclose")


async def run_inspections(
    provider: str | ChatProvider,
    fixture: str | Fixture = "default",
    api_key: str = "",
    system_name: str = "",
    system_version: str = "1.0",
    endpoint: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    timeout: int = 30,
    max_retries: int = 3,
    progress_callback: object = None,
    pipeline_config: EvaluationPipelineConfig | None = None,
    judge_config: JudgeConfig | None = None,
    governor: "ConcurrencyGovernor | None" = None,
    sut_temperature: float = 0.0,
    sut_seed: int | None = None,
    run_nonce: str | None = None,
    holdout_ids: dict[str, str] | None = None,
) -> TestRunResult:
    fixture_obj = _resolve_fixture(fixture)
    provider_obj = _resolve_provider_with_governance(provider, fixture_obj)
    try:
        return await run_all(
            provider=provider_obj,
            config=_build_config(
                provider,
                api_key,
                endpoint,
                model,
                system_prompt,
                timeout,
                max_retries,
                sut_temperature,
                sut_seed,
                run_nonce,
                holdout_ids,
            ),
            fixture=fixture_obj,
            system_name=system_name,
            system_version=system_version,
            progress_callback=progress_callback,
            judge_config=judge_config,
            pipeline_config=pipeline_config,
            governor=governor,
        )
    finally:
        await _aclose_provider(provider_obj)


async def run_strategic(
    provider: str | ChatProvider,
    fixture: str | Fixture = "default",
    api_key: str = "",
    system_name: str = "",
    system_version: str = "1.0",
    endpoint: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    timeout: int = 30,
    max_retries: int = 3,
    progress_callback: object = None,
    pipeline_config: EvaluationPipelineConfig | None = None,
    judge_config: JudgeConfig | None = None,
    governor: "ConcurrencyGovernor | None" = None,
    sut_temperature: float = 0.0,
    sut_seed: int | None = None,
    run_nonce: str | None = None,
    holdout_ids: dict[str, str] | None = None,
) -> TestRunResult:
    fixture_obj = _resolve_fixture(fixture)
    provider_obj = _resolve_provider_with_governance(provider, fixture_obj)
    try:
        return await _run_strategic(
            provider=provider_obj,
            config=_build_config(
                provider,
                api_key,
                endpoint,
                model,
                system_prompt,
                timeout,
                max_retries,
                sut_temperature,
                sut_seed,
                run_nonce,
                holdout_ids,
            ),
            fixture=fixture_obj,
            system_name=system_name,
            system_version=system_version,
            progress_callback=progress_callback,
            judge_config=judge_config,
            pipeline_config=pipeline_config,
            governor=governor,
        )
    finally:
        await _aclose_provider(provider_obj)


async def run_selected(
    test_ids: set[str],
    provider: str | ChatProvider,
    fixture: str | Fixture = "default",
    api_key: str = "",
    system_name: str = "",
    system_version: str = "1.0",
    endpoint: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    timeout: int = 30,
    max_retries: int = 3,
    progress_callback: object = None,
    pipeline_config: EvaluationPipelineConfig | None = None,
    judge_config: JudgeConfig | None = None,
    governor: "ConcurrencyGovernor | None" = None,
    sut_temperature: float = 0.0,
    sut_seed: int | None = None,
    run_nonce: str | None = None,
    holdout_ids: dict[str, str] | None = None,
) -> TestRunResult:
    fixture_obj = _resolve_fixture(fixture)
    provider_obj = _resolve_provider_with_governance(provider, fixture_obj)
    try:
        return await _run_selected(
            test_ids=test_ids,
            provider=provider_obj,
            config=_build_config(
                provider,
                api_key,
                endpoint,
                model,
                system_prompt,
                timeout,
                max_retries,
                sut_temperature,
                sut_seed,
                run_nonce,
                holdout_ids,
            ),
            fixture=fixture_obj,
            system_name=system_name,
            system_version=system_version,
            progress_callback=progress_callback,
            judge_config=judge_config,
            pipeline_config=pipeline_config,
            governor=governor,
        )
    finally:
        await _aclose_provider(provider_obj)


async def run_single(
    test_id: str,
    provider: str | ChatProvider,
    fixture: str | Fixture = "default",
    api_key: str = "",
    endpoint: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
    timeout: int = 30,
    max_retries: int = 3,
    pipeline_config: EvaluationPipelineConfig | None = None,
    judge_config: JudgeConfig | None = None,
    sut_temperature: float = 0.0,
    sut_seed: int | None = None,
    run_nonce: str | None = None,
    holdout_ids: dict[str, str] | None = None,
) -> TestResult:
    fixture_obj = _resolve_fixture(fixture)
    provider_obj = _resolve_provider_with_governance(provider, fixture_obj)
    try:
        return await _run_single(
            test_id=test_id,
            provider=provider_obj,
            config=_build_config(
                provider,
                api_key,
                endpoint,
                model,
                system_prompt,
                timeout,
                max_retries,
                sut_temperature,
                sut_seed,
                run_nonce,
                holdout_ids,
            ),
            fixture=fixture_obj,
            judge_config=judge_config,
            pipeline_config=pipeline_config,
        )
    finally:
        await _aclose_provider(provider_obj)


def compare_scorecards(
    baseline: TestRunResult, enhanced: TestRunResult
) -> ComparisonReport:
    return _compare_scorecards(baseline, enhanced)


def list_tests() -> list[InspectionSpec]:
    return list(ALL_SPECS)


def list_fixtures() -> list[str]:
    return list_fixture_names()
