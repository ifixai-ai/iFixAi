import pytest

from ifixai.core.types import ProviderConfig
from ifixai.judge.config import JudgeConfig
from ifixai.providers.orcarouter import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OrcaRouterProvider,
)
from ifixai.providers.resolver import (
    credential_env_vars,
    detect_available_credentials,
    resolve_credential,
    resolve_provider,
    select_cross_provider_judge,
)
from ifixai.providers.secrets import scrub_secrets
from ifixai.reporting.scorecard import self_judge_bias_applies


def test_orcarouter_resolves_and_reads_credentials() -> None:
    provider = resolve_provider("orcarouter")

    assert isinstance(provider, OrcaRouterProvider)
    assert credential_env_vars("orcarouter") == ("ORCAROUTER_API_KEY",)
    assert resolve_credential(
        "orcarouter",
        {"ORCAROUTER_API_KEY": "sk-orca-test-value-for-orcarouter"},
    ) == "sk-orca-test-value-for-orcarouter"
    assert "orcarouter" in detect_available_credentials(
        {"ORCAROUTER_API_KEY": "sk-orca-test-value-for-orcarouter"}
    )


def test_orcarouter_can_be_selected_as_independent_judge() -> None:
    assert select_cross_provider_judge("openai", ["openai", "orcarouter"]) == "orcarouter"
    assert select_cross_provider_judge("orcarouter", ["openai", "orcarouter"]) == "openai"


@pytest.mark.asyncio
async def test_orcarouter_client_uses_openai_compatible_defaults() -> None:
    provider = OrcaRouterProvider()
    config = ProviderConfig(
        provider="orcarouter", api_key="sk-orca-test-value-for-orcarouter"
    )

    try:
        client = await provider.get_client(config)

        assert str(client.base_url) == f"{DEFAULT_BASE_URL}/"
        assert (
            provider._clients[
                (
                    DEFAULT_BASE_URL,
                    config.api_key,
                    float(config.timeout),
                    config.max_retries,
                )
            ]
            is client
        )
        assert DEFAULT_MODEL == "openai/gpt-4o"
    finally:
        await provider.aclose()


def test_orcarouter_keys_are_scrubbed() -> None:
    assert (
        scrub_secrets("token sk-orca-abcdefghijklmnopqrstuvwxyz")
        == "token ***REDACTED_ORCAROUTER_KEY***"
    )
    assert scrub_secrets("break-glass-escalation-path") == "break-glass-escalation-path"
    # sk-orca- must win over the generic sk-or- (OpenRouter) rule.
    assert (
        scrub_secrets("token sk-orca-abcdefghijklmnopqrstuvwxyz")
        != "token ***REDACTED_OPENROUTER_KEY***"
    )


def test_orcarouter_is_treated_as_aggregator_for_bias_detection() -> None:
    assert not self_judge_bias_applies(
        JudgeConfig(provider="orcarouter", model="anthropic/claude-sonnet-4.6"),
        "openai",
        "gpt-4o",
    )
    assert self_judge_bias_applies(
        JudgeConfig(provider="orcarouter", model="openai/gpt-4o"),
        "openai",
        "gpt-4o",
    )
