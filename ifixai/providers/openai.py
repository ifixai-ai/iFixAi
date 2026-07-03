import asyncio

import openai

from ifixai.providers.base import (
    ChatProvider,
    create_chat_completion_json_fallback,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from ifixai.core.types import ChatMessage, ProviderConfig

DEFAULT_MODEL = "gpt-4o"

ClientCacheKey = tuple[str | None, str | None, float, int]


class OpenAIProvider(ChatProvider):

    def __init__(self) -> None:
        self._clients: dict[ClientCacheKey, openai.AsyncOpenAI] = {}
        self._client_lock = asyncio.Lock()

    async def get_client(self, config: ProviderConfig) -> openai.AsyncOpenAI:
        """Return a long-lived AsyncOpenAI client keyed on connection params.

        Caching one client per (endpoint, api_key, timeout, max_retries)
        keeps the underlying httpx connection pool warm across hundreds of
        LLM calls per run instead of re-handshaking TLS for every request.
        """
        key: ClientCacheKey = (
            config.endpoint,
            config.api_key,
            float(config.timeout),
            config.max_retries,
        )
        cached = self._clients.get(key)
        if cached is not None:
            return cached
        async with self._client_lock:
            cached = self._clients.get(key)
            if cached is not None:
                return cached
            client = openai.AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.endpoint,
                timeout=float(config.timeout),
                max_retries=config.max_retries,
            )
            self._clients[key] = client
            return client

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        model = config.model or DEFAULT_MODEL
        formatted_messages = [{"role": m.role, "content": m.content} for m in messages]

        endpoint = config.endpoint or "https://api.openai.com/v1"

        client = await self.get_client(config)
        params: dict[str, object] = {
            "model": model,
            "messages": formatted_messages,
            "temperature": config.temperature,
        }
        if config.seed is not None:
            params["seed"] = config.seed
        if config.max_tokens is not None:
            params["max_tokens"] = config.max_tokens
        if config.json_output:
            # Constrain judge calls to valid JSON (cheap models reliably emit a
            # parseable verdict); fall back to free text if unsupported.
            params["response_format"] = {"type": "json_object"}
        try:
            response = await create_chat_completion_json_fallback(client, **params)

            choices = response.choices
            if not choices:
                raise ProviderResponseError(
                    provider="openai",
                    endpoint=endpoint,
                    details=f"No choices in response (id={response.id})",
                )
            choice = choices[0]
            finish_reason = choice.finish_reason or "unknown"
            if choice.message is None:
                raise ProviderResponseError(
                    provider="openai",
                    endpoint=endpoint,
                    details=f"Missing message in choice (finish_reason={finish_reason})",
                )
            content = choice.message.content
            if not content:
                raise ProviderEmptyContentError(
                    provider="openai",
                    endpoint=endpoint,
                    details=f"Empty content in response (finish_reason={finish_reason})",
                )
            return content

        except openai.AuthenticationError as exc:
            raise ProviderAuthError(
                provider="openai",
                endpoint=endpoint,
                details=str(exc),
            ) from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError(
                provider="openai",
                endpoint=endpoint,
                details=str(exc),
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderConnectionError(
                provider="openai",
                endpoint=endpoint,
                details=str(exc),
            ) from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError(
                provider="openai",
                endpoint=endpoint,
                details=str(exc),
            ) from exc
        except openai.APIError as exc:
            raise ProviderResponseError(
                provider="openai",
                endpoint=endpoint,
                details=str(exc),
            ) from exc
