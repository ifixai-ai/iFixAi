import asyncio
import json
from typing import Any

import aiohttp
import pytest

from ifixai.cli.init import PROVIDER_ENV_KEYS
from ifixai.cli.model_catalog import default_model, suggestions
from ifixai.cli.run import PROVIDER_CHOICES
from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from ifixai.providers.minimax import (
    ANTHROPIC_VERSION,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    MODEL_METADATA,
    MODEL_PRICING_TIERS,
    REGIONAL_ENDPOINTS,
    MiniMaxProvider,
    _build_request,
    _extract_response_text,
)
from ifixai.providers.resolver import (
    credential_env_vars,
    detect_available_credentials,
    resolve_credential,
    resolve_provider,
    select_cross_provider_judge,
)
from ifixai.providers.secrets import looks_like_secret, scrub_secrets


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


class _FakeRequestContext:
    def __init__(self, outcome: _FakeResponse | BaseException) -> None:
        self._outcome = outcome

    async def __aenter__(self) -> _FakeResponse:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, outcomes: list[_FakeResponse | BaseException]) -> None:
        self.closed = False
        self._outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeRequestContext:
        self.calls.append({"url": url, **kwargs})
        return _FakeRequestContext(self._outcomes.pop(0))

    async def close(self) -> None:
        self.closed = True


def test_minimax_is_registered_with_dedicated_credentials() -> None:
    provider = resolve_provider("minimax")

    assert isinstance(provider, MiniMaxProvider)
    assert credential_env_vars("minimax") == ("MINIMAX_API_KEY",)
    assert (
        resolve_credential(
            "minimax",
            {"MINIMAX_API_KEY": "test-key"},
        )
        == "test-key"
    )
    assert "minimax" in detect_available_credentials({"MINIMAX_API_KEY": "test-key"})
    assert PROVIDER_ENV_KEYS["minimax"] == "MINIMAX_API_KEY"
    assert "minimax" in PROVIDER_CHOICES
    assert (
        select_cross_provider_judge("atlascloud", ["atlascloud", "minimax"])
        == "minimax"
    )


def test_minimax_catalog_and_metadata_match_current_models() -> None:
    assert default_model("minimax") == DEFAULT_MODEL
    assert [model_id for model_id, _ in suggestions("minimax")] == [
        "MiniMax-M3",
        "MiniMax-M2.7",
    ]
    assert MODEL_METADATA == {
        "MiniMax-M3": {
            "context_window": 1000000,
            "pricing_usd_per_million_tokens": {
                "input": 0.6,
                "output": 2.4,
                "cache_read": 0.12,
                "cache_write": None,
            },
            "input_modalities": ["text", "image", "video"],
            "thinking": ["adaptive", "disabled"],
        },
        "MiniMax-M2.7": {
            "context_window": 204800,
            "pricing_usd_per_million_tokens": {
                "input": 0.3,
                "output": 1.2,
                "cache_read": 0.06,
                "cache_write": 0.375,
            },
            "input_modalities": ["text"],
            "thinking": ["always_on"],
        },
    }
    assert MODEL_PRICING_TIERS["MiniMax-M3"] == [
        {
            "max_input_tokens": 512000,
            "pricing_usd_per_million_tokens": {
                "input": 0.3,
                "output": 1.2,
                "cache_read": 0.06,
                "cache_write": None,
            },
        },
        {
            "max_input_tokens": None,
            "pricing_usd_per_million_tokens": {
                "input": 0.6,
                "output": 2.4,
                "cache_read": 0.12,
                "cache_write": None,
            },
        },
    ]
    m3_description = dict(suggestions("minimax"))["MiniMax-M3"]
    assert "$0.30/$1.20 up to 512k input" in m3_description
    assert "$0.60/$2.40 above 512k input" in m3_description


def test_minimax_regional_endpoints_are_complete() -> None:
    assert REGIONAL_ENDPOINTS == {
        "global_en": {
            "openai_base_url": "https://api.minimax.io/v1",
            "anthropic_base_url": "https://api.minimax.io/anthropic",
            "docs_root": "https://platform.minimax.io/docs",
        },
        "cn_zh": {
            "openai_base_url": "https://api.minimaxi.com/v1",
            "anthropic_base_url": "https://api.minimaxi.com/anthropic",
            "docs_root": "https://platform.minimaxi.com/docs",
        },
    }
    assert DEFAULT_BASE_URL == REGIONAL_ENDPOINTS["global_en"]["anthropic_base_url"]


def test_minimax_builds_messages_request_for_default_endpoint() -> None:
    config = ProviderConfig(provider="minimax", api_key="test-key")
    messages = [
        ChatMessage(role="system", content="Be precise."),
        ChatMessage(role="user", content="Hello"),
    ]

    endpoint, url, api_style, headers, payload = _build_request(messages, config)

    assert api_style == "messages"
    assert url == f"{endpoint}/v1/messages"
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["anthropic-version"] == ANTHROPIC_VERSION
    assert payload["model"] == DEFAULT_MODEL
    assert payload["system"] == "Be precise."
    assert payload["messages"] == [{"role": "user", "content": "Hello"}]


def test_minimax_builds_chat_completions_request_for_cn_endpoint() -> None:
    endpoint = REGIONAL_ENDPOINTS["cn_zh"]["openai_base_url"]
    config = ProviderConfig(
        provider="minimax",
        endpoint=endpoint,
        api_key="test-key",
        model="MiniMax-M2.7",
        json_output=True,
    )

    resolved, url, api_style, headers, payload = _build_request(
        [ChatMessage(role="user", content="Hello")],
        config,
    )

    assert resolved == endpoint
    assert api_style == "chat_completions"
    assert url == f"{endpoint}/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert "anthropic-version" not in headers
    assert payload["model"] == "MiniMax-M2.7"
    assert payload["response_format"] == {"type": "json_object"}


def test_minimax_extracts_both_response_styles() -> None:
    assert (
        _extract_response_text(
            {
                "content": [
                    {"type": "thinking", "thinking": "..."},
                    {"type": "text", "text": "Done"},
                ]
            },
            "messages",
            DEFAULT_BASE_URL,
        )
        == "Done"
    )
    assert (
        _extract_response_text(
            {"choices": [{"message": {"content": "Done"}}]},
            "chat_completions",
            REGIONAL_ENDPOINTS["global_en"]["openai_base_url"],
        )
        == "Done"
    )


def test_minimax_subscription_keys_are_scrubbed() -> None:
    assert (
        scrub_secrets("token sk-cp-test-value-for-minimax")
        == "token ***REDACTED_MINIMAX_KEY***"
    )


def test_minimax_standard_keys_are_scrubbed() -> None:
    api_key = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJtaW5pbWF4LXRlc3Qta2V5In0."
        "signature-for-minimax-test-key"
    )

    assert looks_like_secret(api_key)
    assert scrub_secrets(f"token {api_key}") == "token ***REDACTED_MINIMAX_KEY***"


@pytest.mark.asyncio
async def test_minimax_send_message_posts_messages_request() -> None:
    provider = MiniMaxProvider()
    session = _FakeSession(
        [_FakeResponse(200, '{"content":[{"type":"text","text":"Done"}]}')]
    )
    provider._session = session  # type: ignore[assignment]

    result = await provider.send_message(
        [ChatMessage(role="user", content="Hello")],
        ProviderConfig(provider="minimax", api_key="test-key", max_retries=0),
    )

    assert result == "Done"
    assert session.calls[0]["url"] == f"{DEFAULT_BASE_URL}/v1/messages"
    assert session.calls[0]["headers"]["anthropic-version"] == ANTHROPIC_VERSION
    assert session.calls[0]["json"]["model"] == DEFAULT_MODEL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "error_type"),
    [
        (401, "unauthorized", ProviderAuthError),
        (403, "forbidden", ProviderAuthError),
        (429, "rate limited", ProviderRateLimitError),
        (500, "upstream failed", ProviderResponseError),
    ],
)
async def test_minimax_maps_http_errors(
    status: int,
    body: str,
    error_type: type[Exception],
) -> None:
    provider = MiniMaxProvider()
    provider._session = _FakeSession([_FakeResponse(status, body)])  # type: ignore[assignment]

    with pytest.raises(error_type):
        await provider.send_message(
            [ChatMessage(role="user", content="Hello")],
            ProviderConfig(provider="minimax", max_retries=0),
        )


@pytest.mark.asyncio
async def test_minimax_scrubs_standard_key_from_error_response() -> None:
    api_key = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJtaW5pbWF4LXRlc3Qta2V5In0."
        "signature-for-minimax-test-key"
    )
    provider = MiniMaxProvider()
    provider._session = _FakeSession(  # type: ignore[assignment]
        [_FakeResponse(500, f'{{"error":"invalid key {api_key}"}}')]
    )

    with pytest.raises(ProviderResponseError) as exc_info:
        await provider.send_message(
            [ChatMessage(role="user", content="Hello")],
            ProviderConfig(provider="minimax", max_retries=0),
        )

    assert api_key not in str(exc_info.value)
    assert "***REDACTED_MINIMAX_KEY***" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "error_type"),
    [
        ("not-json", ProviderResponseError),
        ("[]", ProviderResponseError),
        ('{"content":[]}', ProviderEmptyContentError),
    ],
)
async def test_minimax_maps_invalid_responses(
    body: str,
    error_type: type[Exception],
) -> None:
    provider = MiniMaxProvider()
    provider._session = _FakeSession([_FakeResponse(200, body)])  # type: ignore[assignment]

    with pytest.raises(error_type):
        await provider.send_message(
            [ChatMessage(role="user", content="Hello")],
            ProviderConfig(provider="minimax", max_retries=0),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream_error", "error_type"),
    [
        (aiohttp.ClientConnectionError("offline"), ProviderConnectionError),
        (asyncio.TimeoutError(), ProviderTimeoutError),
    ],
)
async def test_minimax_maps_transport_errors(
    upstream_error: BaseException,
    error_type: type[Exception],
) -> None:
    provider = MiniMaxProvider()
    provider._session = _FakeSession([upstream_error])  # type: ignore[assignment]

    with pytest.raises(error_type):
        await provider.send_message(
            [ChatMessage(role="user", content="Hello")],
            ProviderConfig(provider="minimax", max_retries=0),
        )


@pytest.mark.asyncio
async def test_minimax_retries_transient_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MiniMaxProvider()
    session = _FakeSession(
        [
            _FakeResponse(429, "rate limited"),
            _FakeResponse(
                200, json.dumps({"content": [{"type": "text", "text": "Done"}]})
            ),
        ]
    )
    provider._session = session  # type: ignore[assignment]

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    result = await provider.send_message(
        [ChatMessage(role="user", content="Hello")],
        ProviderConfig(provider="minimax", max_retries=1),
    )

    assert result == "Done"
    assert len(session.calls) == 2
