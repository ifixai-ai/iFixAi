import asyncio
import base64
import json
import os
from typing import Any

import aiohttp

from ifixai.providers.base import (
    ChatProvider,
    ProviderConnectionError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from ifixai.providers.secrets import scrub_secrets
from ifixai.core.types import ChatMessage, ProviderConfig, RetrievedSource

DEFAULT_ENDPOINT = os.environ.get("IFIXAI_HTTP_ENDPOINT", "http://localhost:8000/v1")
EXTRA_HEADERS_ENV_VAR = "IFIXAI_EXTRA_HEADERS"


def _load_env_extra_headers() -> dict[str, str]:
    raw = os.environ.get(EXTRA_HEADERS_ENV_VAR)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def _build_auth_headers(config: ProviderConfig) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        auth_method = config.auth_method
        if auth_method == "basic":
            encoded = base64.b64encode(config.api_key.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        elif auth_method == "api_key":
            headers["X-API-Key"] = config.api_key
        elif auth_method != "none":
            headers["Authorization"] = f"Bearer {config.api_key}"
    headers.update(_load_env_extra_headers())
    if config.extra_headers:
        headers.update(config.extra_headers)
    return headers


def _source_item_to_retrieved(item: dict[str, Any]) -> RetrievedSource:
    return RetrievedSource(
        source_id=str(
            item.get("document_uri")
            or item.get("source_id")
            or item.get("document_name")
            or ""
        ),
        source_name=str(item.get("document_name") or item.get("source_name") or ""),
        source_type=str(item.get("source_type") or ""),
        relevance_score=float(item.get("relevance_score") or 0.0),
        content_snippet=str(item.get("text") or item.get("content_snippet") or ""),
        metadata={
            k: v
            for k, v in item.items()
            if k
            not in {
                "document_uri",
                "document_name",
                "text",
                "relevance_score",
                "source_type",
            }
        },
    )


class HttpProvider(ChatProvider):

    def __init__(self) -> None:
        self._last_sources: dict[str, list[RetrievedSource]] = {}
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        """Return a long-lived aiohttp session shared across calls.

        Reusing one ClientSession amortizes TCP/TLS setup across hundreds of
        LLM round trips per run. The session is created lazily on first use
        and released by aclose() at orchestrator teardown.
        """
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
        endpoint = (config.endpoint or DEFAULT_ENDPOINT).rstrip("/")
        url = f"{endpoint}/chat/completions"

        payload: dict[str, Any] = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": config.temperature,
        }
        if config.model:
            payload["model"] = config.model
        if config.seed is not None:
            payload["seed"] = config.seed
        if config.max_tokens is not None:
            payload["max_tokens"] = config.max_tokens

        headers = _build_auth_headers(config)

        timeout = aiohttp.ClientTimeout(total=config.timeout)

        last_error: Exception | None = None
        for attempt in range(config.max_retries + 1):
            try:
                return await self._send_request(url, payload, headers, timeout, config)
            except ProviderRateLimitError as exc:
                last_error = exc
                if attempt < config.max_retries:
                    await asyncio.sleep(2**attempt)
                    continue
                raise
            except (ProviderConnectionError, ProviderTimeoutError) as exc:
                last_error = exc
                if attempt < config.max_retries:
                    await asyncio.sleep(2**attempt)
                    continue
                raise

        raise last_error or ProviderConnectionError(
            provider="http", endpoint=endpoint, details="Max retries exhausted"
        )

    async def _send_request(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: aiohttp.ClientTimeout,
        config: ProviderConfig,
    ) -> str:
        endpoint = config.endpoint or DEFAULT_ENDPOINT
        session = await self.get_session()
        try:
            async with session.post(
                url, json=payload, headers=headers, timeout=timeout
            ) as resp:
                if resp.status == 401 or resp.status == 403:
                    raise ProviderAuthError(
                        provider="http",
                        endpoint=endpoint,
                        details=f"HTTP {resp.status}: authentication failed",
                    )
                if resp.status == 429:
                    raise ProviderRateLimitError(
                        provider="http",
                        endpoint=endpoint,
                        details="HTTP 429: rate limited",
                    )
                if resp.status >= 400:
                    body = await resp.text()
                    raise ProviderResponseError(
                        provider="http",
                        endpoint=endpoint,
                        details=f"HTTP {resp.status}: {scrub_secrets(body[:500])}",
                    )

                data = await resp.json()
                self._capture_sources(endpoint.rstrip("/"), data)
                return self._extract_response_text(data, endpoint)

        except aiohttp.ClientConnectionError as exc:
            raise ProviderConnectionError(
                provider="http",
                endpoint=endpoint,
                details=str(exc),
            ) from exc
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                provider="http",
                endpoint=endpoint,
                details=f"Request timed out after {config.timeout}s",
            ) from exc

    def _extract_response_text(self, data: dict[str, Any], endpoint: str) -> str:
        try:
            choices = data.get("choices", [])
            if not choices:
                raise ProviderResponseError(
                    provider="http",
                    endpoint=endpoint,
                    details="No choices in response",
                )
            message = choices[0].get("message", {})
            content = message.get("content", "")
            if not content:
                raise ProviderResponseError(
                    provider="http",
                    endpoint=endpoint,
                    details="Empty content in response",
                )
            return content
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderResponseError(
                provider="http",
                endpoint=endpoint,
                details=f"Unexpected response format: {exc}",
            ) from exc

    def _capture_sources(self, endpoint: str, data: dict[str, Any]) -> None:
        raw_sources = data.get("sources")
        if not isinstance(raw_sources, list):
            return
        parsed = [
            _source_item_to_retrieved(s) for s in raw_sources if isinstance(s, dict)
        ]
        self._last_sources[endpoint] = parsed

    async def retrieve_sources(
        self,
        query: str,
        config: ProviderConfig,
    ) -> list[RetrievedSource] | None:
        endpoint = (config.endpoint or DEFAULT_ENDPOINT).rstrip("/")
        url = f"{endpoint}/retrieve"
        headers = _build_auth_headers(config)
        timeout = aiohttp.ClientTimeout(total=config.timeout)
        payload: dict[str, Any] = {"query": query}

        session = await self.get_session()
        try:
            async with session.post(
                url, json=payload, headers=headers, timeout=timeout
            ) as resp:
                if resp.status >= 400:
                    return self._last_sources.get(endpoint)
                data = await resp.json()
                raw = data.get("sources")
                if not isinstance(raw, list):
                    return self._last_sources.get(endpoint)
                return [
                    _source_item_to_retrieved(s) for s in raw if isinstance(s, dict)
                ]
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return self._last_sources.get(endpoint)
