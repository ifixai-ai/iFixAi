import pytest

from ifixai.core.types import ProviderConfig
from ifixai.judge.config import JudgeConfig
from ifixai.providers.requesty import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    RequestyProvider,
)
from ifixai.providers.resolver import (
    REGISTERED_PROVIDERS,
    credential_env_vars,
    detect_available_credentials,
    resolve_credential,
    resolve_provider,
)
from ifixai.providers.secrets import looks_like_secret, scrub_secrets
from ifixai.reporting.scorecard import self_judge_bias_applies


def test_requesty_resolves_and_reads_credentials() -> None:
    provider = resolve_provider("requesty")

    assert isinstance(provider, RequestyProvider)
    assert "requesty" in REGISTERED_PROVIDERS
    assert credential_env_vars("requesty") == ("REQUESTY_API_KEY",)
    assert (
        resolve_credential("requesty", {"REQUESTY_API_KEY": "test-value-for-requesty"})
        == "test-value-for-requesty"
    )
    assert "requesty" in detect_available_credentials(
        {"REQUESTY_API_KEY": "test-value-for-requesty"}
    )



@pytest.mark.asyncio
async def test_requesty_client_uses_openai_compatible_defaults() -> None:
    provider = RequestyProvider()
    config = ProviderConfig(provider="requesty", api_key="test-value-for-requesty")

    try:
        client = await provider.get_client(config)

        assert str(client.base_url) == f"{DEFAULT_BASE_URL}/"
        assert DEFAULT_BASE_URL == "https://router.requesty.ai/v1"
        assert provider._clients[
            (DEFAULT_BASE_URL, config.api_key, float(config.timeout), config.max_retries)
        ] is client
        assert await provider.get_client(config) is client
        assert DEFAULT_MODEL == "openai/gpt-4o-mini"
    finally:
        await provider.aclose()


def test_requesty_keys_are_scrubbed() -> None:
    key = "rqsty-ab-cdefghijklmnopqrstuvwxyz+ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789="
    assert scrub_secrets(f"token {key}") == "token ***REDACTED_REQUESTY_KEY***"
    assert looks_like_secret(key)
    assert scrub_secrets("rqsty-short") == "rqsty-short"


def test_requesty_is_treated_as_aggregator_for_bias_detection() -> None:
    assert not self_judge_bias_applies(
        JudgeConfig(provider="requesty", model="anthropic/claude-sonnet-4-6"),
        "openai",
        "gpt-4o",
    )
    assert self_judge_bias_applies(
        JudgeConfig(provider="requesty", model="openai/gpt-4o"),
        "openai",
        "gpt-4o",
    )
