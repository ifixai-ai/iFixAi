import asyncio

import aiohttp

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    RETRYABLE_HTTP_STATUS_CODES,
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderError,
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from ifixai.providers.http import _build_auth_headers

DEFAULT_ENDPOINT = "http://localhost:8000"


class LangChainProvider(ChatProvider):
    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        endpoint = (config.endpoint or DEFAULT_ENDPOINT).rstrip("/")
        url = f"{endpoint}/invoke"
        timeout = aiohttp.ClientTimeout(total=config.timeout)

        config_overrides: dict = {"temperature": config.temperature}
        if config.seed is not None:
            config_overrides["seed"] = config.seed
        if config.max_tokens is not None:
            config_overrides["max_tokens"] = config.max_tokens

        payload = {
            "input": {
                "messages": [{"role": m.role, "content": m.content} for m in messages],
            },
            "config": {"configurable": config_overrides},
        }

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=_build_auth_headers(config)) as response:
                    if response.status == 401 or response.status == 403:
                        raise ProviderAuthError(
                            provider="langchain",
                            endpoint=url,
                            details=f"Authentication failed (HTTP {response.status})",
                        )
                    response.raise_for_status()
                    data = await response.json()

                    if not isinstance(data, dict) or "output" not in data or data["output"] is None:
                        raise ProviderResponseError(
                            provider="langchain", endpoint=url,
                            details="Missing or invalid output in response",
                        )
                    output = data["output"]
                    if isinstance(output, str):
                        return _validate_text(output, url)
                    if isinstance(output, dict):
                        content = output.get("content", str(output))
                        if isinstance(content, list):
                            # AIMessage.content may contain ordered strings and
                            # multimodal blocks. Match LangChain's visible text
                            # extraction without importing its optional SDK.
                            text_parts = []
                            for block in content:
                                if isinstance(block, str):
                                    text_parts.append(block)
                                elif isinstance(block, dict) and block.get("type") == "text":
                                    text = block.get("text")
                                    if isinstance(text, str):
                                        text_parts.append(text)
                            text = "".join(text_parts)
                            if not text:
                                raise ProviderEmptyContentError(
                                    provider="langchain",
                                    endpoint=url,
                                    details="No text content in response message",
                                )
                            return text
                        return _validate_text(content, url)
                    return str(output)

        except aiohttp.ClientResponseError as exc:
            error_class: type[ProviderError] = ProviderResponseError
            if exc.status == 429:
                error_class = ProviderRateLimitError
            elif exc.status in RETRYABLE_HTTP_STATUS_CODES:
                error_class = ProviderOverloadedError
            raise error_class(
                provider="langchain",
                endpoint=url,
                details=f"HTTP {exc.status}: {exc.message}",
            ) from exc
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                provider="langchain",
                endpoint=url,
                details=f"Request timed out after {config.timeout}s",
            ) from exc
        except aiohttp.ClientError as exc:
            raise ProviderConnectionError(
                provider="langchain",
                endpoint=url,
                details=str(exc),
            ) from exc


def _validate_text(content: object, endpoint: str) -> str:
    if not isinstance(content, str):
        raise ProviderResponseError(
            provider="langchain", endpoint=endpoint,
            details="Non-text content in response message",
        )
    if not content:
        raise ProviderEmptyContentError(
            provider="langchain", endpoint=endpoint,
            details="Empty content in response message",
        )
    return content
