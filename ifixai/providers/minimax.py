import asyncio
import json
from typing import Any, Final, Literal, TypedDict

import aiohttp

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from ifixai.providers.secrets import scrub_secrets


class PricingMetadata(TypedDict):
    input: float
    output: float
    cache_read: float
    cache_write: float | None


class ModelMetadata(TypedDict):
    context_window: int
    pricing_usd_per_million_tokens: PricingMetadata
    input_modalities: list[str]
    thinking: list[str]


class PricingTier(TypedDict):
    max_input_tokens: int | None
    pricing_usd_per_million_tokens: PricingMetadata


class RegionalEndpoint(TypedDict):
    openai_base_url: str
    anthropic_base_url: str
    docs_root: str


REGIONAL_ENDPOINTS: Final[dict[str, RegionalEndpoint]] = {
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

MODEL_METADATA: Final[dict[str, ModelMetadata]] = {
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

MODEL_PRICING_TIERS: Final[dict[str, list[PricingTier]]] = {
    "MiniMax-M3": [
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
    ],
}

DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_BASE_URL = REGIONAL_ENDPOINTS["global_en"]["anthropic_base_url"]
DEFAULT_MAX_TOKENS = 4096
ANTHROPIC_VERSION = "2023-06-01"

APIStyle = Literal["messages", "chat_completions"]


def _api_style(endpoint: str) -> APIStyle:
    normalized = endpoint.rstrip("/")
    messages_endpoints = {
        values["anthropic_base_url"].rstrip("/")
        for values in REGIONAL_ENDPOINTS.values()
    }
    if normalized in messages_endpoints or normalized.endswith("/anthropic"):
        return "messages"
    return "chat_completions"


def _build_request(
    messages: list[ChatMessage],
    config: ProviderConfig,
) -> tuple[str, str, APIStyle, dict[str, str], dict[str, Any]]:
    endpoint = (config.endpoint or DEFAULT_BASE_URL).rstrip("/")
    api_style = _api_style(endpoint)
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    model = config.model or DEFAULT_MODEL
    if api_style == "messages":
        headers["anthropic-version"] = ANTHROPIC_VERSION
        system_text = "\n".join(
            message.content for message in messages if message.role == "system"
        )
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": config.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
                if message.role != "system"
            ],
            "temperature": config.temperature,
        }
        if system_text:
            payload["system"] = system_text
        url = f"{endpoint}/v1/messages"
    else:
        payload = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "temperature": config.temperature,
        }
        if config.max_tokens is not None:
            payload["max_tokens"] = config.max_tokens
        if config.seed is not None:
            payload["seed"] = config.seed
        if config.json_output:
            payload["response_format"] = {"type": "json_object"}
        url = f"{endpoint}/chat/completions"

    headers.update(config.extra_headers)
    return endpoint, url, api_style, headers, payload


def _extract_response_text(
    data: dict[str, Any],
    api_style: APIStyle,
    endpoint: str,
) -> str:
    if api_style == "messages":
        blocks = data.get("content")
        if not isinstance(blocks, list):
            blocks = []
        text_parts = [
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
            and block["text"]
        ]
        if text_parts:
            return "\n".join(text_parts)
    else:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        return content

    raise ProviderEmptyContentError(
        provider="minimax",
        endpoint=endpoint,
        details="Response did not contain text content",
    )


class MiniMaxProvider(ChatProvider):
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                return self._session
            self._session = aiohttp.ClientSession()
            return self._session

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        endpoint, url, api_style, headers, payload = _build_request(messages, config)
        timeout = aiohttp.ClientTimeout(total=config.timeout)
        last_error: Exception | None = None

        for attempt in range(config.max_retries + 1):
            try:
                return await self._send_request(
                    endpoint,
                    url,
                    api_style,
                    headers,
                    payload,
                    timeout,
                )
            except (
                ProviderConnectionError,
                ProviderRateLimitError,
                ProviderTimeoutError,
            ) as exc:
                last_error = exc
                if attempt == config.max_retries:
                    raise
                await asyncio.sleep(2**attempt)

        raise last_error or ProviderConnectionError(
            provider="minimax",
            endpoint=endpoint,
            details="Max retries exhausted",
        )

    async def _send_request(
        self,
        endpoint: str,
        url: str,
        api_style: APIStyle,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: aiohttp.ClientTimeout,
    ) -> str:
        session = await self.get_session()
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
            ) as response:
                body = await response.text()
                if response.status in {401, 403}:
                    raise ProviderAuthError(
                        provider="minimax",
                        endpoint=endpoint,
                        details=f"HTTP {response.status}: authentication failed",
                    )
                if response.status == 429:
                    raise ProviderRateLimitError(
                        provider="minimax",
                        endpoint=endpoint,
                        details="HTTP 429: rate limited",
                    )
                if response.status >= 400:
                    raise ProviderResponseError(
                        provider="minimax",
                        endpoint=endpoint,
                        details=f"HTTP {response.status}: {scrub_secrets(body[:500])}",
                    )
                try:
                    data = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise ProviderResponseError(
                        provider="minimax",
                        endpoint=endpoint,
                        details="Response was not valid JSON",
                    ) from exc
                if not isinstance(data, dict):
                    raise ProviderResponseError(
                        provider="minimax",
                        endpoint=endpoint,
                        details="Response JSON was not an object",
                    )
                return _extract_response_text(data, api_style, endpoint)
        except aiohttp.ClientError as exc:
            raise ProviderConnectionError(
                provider="minimax",
                endpoint=endpoint,
                details=str(exc),
            ) from exc
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                provider="minimax",
                endpoint=endpoint,
                details="Request timed out",
            ) from exc
