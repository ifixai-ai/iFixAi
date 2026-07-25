import pytest

from ifixai.core.types import ProviderConfig
from ifixai.judge.config import JudgeConfig
from ifixai.providers.atlascloud import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    AtlasCloudProvider,
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


def test_atlascloud_resolves_and_reads_credentials() -> None:
    provider = resolve_provider("atlascloud")

    assert isinstance(provider, AtlasCloudProvider)
    assert credential_env_vars("atlascloud") == (
        "ATLASCLOUD_API_KEY",
        "ATLAS_CLOUD_API_KEY",
    )
    assert resolve_credential(
        "atlascloud",
        {"ATLASCLOUD_API_KEY": "apikey-test-value-for-atlascloud"},
    ) == "apikey-test-value-for-atlascloud"
    assert "atlascloud" in detect_available_credentials(
        {"ATLAS_CLOUD_API_KEY": "ak-test-value-for-atlascloud"}
    )


def test_atlascloud_can_be_selected_as_independent_judge() -> None:
    assert select_cross_provider_judge("openai", ["openai", "atlascloud"]) == "atlascloud"
    assert select_cross_provider_judge("atlascloud", ["openai", "atlascloud"]) == "openai"


@pytest.mark.asyncio
async def test_atlascloud_client_uses_openai_compatible_defaults() -> None:
    provider = AtlasCloudProvider()
    config = ProviderConfig(provider="atlascloud", api_key="ak-test-value-for-atlascloud")

    try:
        client = await provider.get_client(config)

        assert str(client.base_url) == f"{DEFAULT_BASE_URL}/"
        assert provider._clients[
            (DEFAULT_BASE_URL, config.api_key, float(config.timeout), config.max_retries)
        ] is client
        assert DEFAULT_MODEL == "qwen/qwen3.5-flash"
    finally:
        await provider.aclose()


def test_atlascloud_keys_are_scrubbed() -> None:
    assert (
        scrub_secrets("token ak-abcdefghijklmnopqrstuvwxyz")
        == "token ***REDACTED_ATLASCLOUD_KEY***"
    )
    assert scrub_secrets("break-glass-escalation-path") == "break-glass-escalation-path"
    assert (
        scrub_secrets("token apikey-abcdefghijklmnopqrstuvwxyz")
        == "token apikey-abcdefghijklmnopqrstuvwxyz"
    )


def test_atlascloud_is_treated_as_aggregator_for_bias_detection() -> None:
    assert not self_judge_bias_applies(
        JudgeConfig(provider="atlascloud", model="anthropic/claude-sonnet-4-6"),
        "openai",
        "gpt-4o",
    )
    assert self_judge_bias_applies(
        JudgeConfig(provider="atlascloud", model="openai/gpt-4o"),
        "openai",
        "gpt-4o",
    )
