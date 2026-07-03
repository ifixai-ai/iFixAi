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

DEFAULT_MODEL = "openai/gpt-4o"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# Hard ceiling on max_tokens for OpenRouter calls. Per-call ``config.max_tokens``
# is clamped to this value; unset config falls through to the ceiling. Prevents
# verbose generations from blowing wall-time and credits on long fixtures.
MAX_TOKENS_CEILING: int = 2048

ClientCacheKey = tuple[str, str | None, float, int]


class OpenRouterProvider(ChatProvider):

    def __init__(self) -> None:
        self._clients: dict[ClientCacheKey, openai.AsyncOpenAI] = {}
        self._client_lock = asyncio.Lock()

    async def get_client(self, config: ProviderConfig) -> openai.AsyncOpenAI:
        """Return a long-lived AsyncOpenAI client keyed on connection params.

        Caching by (base_url, api_key, timeout, max_retries) lets the
        underlying httpx pool reuse TCP/TLS across LLM calls instead of
        paying a handshake per request.
        """
        base_url = config.endpoint or DEFAULT_BASE_URL
        key: ClientCacheKey = (
            base_url,
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
                base_url=base_url,
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
        base_url = config.endpoint or DEFAULT_BASE_URL
        model = config.model or DEFAULT_MODEL
        formatted = [{"role": m.role, "content": m.content} for m in messages]

        client = await self.get_client(config)
        try:
            effective_max_tokens = (
                min(config.max_tokens, MAX_TOKENS_CEILING)
                if config.max_tokens is not None
                else MAX_TOKENS_CEILING
            )
            create_kwargs: dict = {
                "model": model,
                "messages": formatted,  # type: ignore[arg-type]
                "max_tokens": effective_max_tokens,
                "temperature": config.temperature,
            }
            if config.seed is not None:
                create_kwargs["seed"] = config.seed
            if config.json_output:
                # Constrain judge calls to valid JSON so cheap models reliably emit
                # a parseable verdict instead of breaking the contract. Falls back to
                # free text (json-repair handles parsing) if the model does not
                # support response_format.
                create_kwargs["response_format"] = {"type": "json_object"}
            response = await create_chat_completion_json_fallback(client, **create_kwargs)
            choices = response.choices
            if not choices:
                raise ProviderResponseError(
                    provider="openrouter",
                    endpoint=base_url,
                    details=f"No choices in response (id={response.id})",
                )
            choice = choices[0]
            finish_reason = choice.finish_reason or "unknown"
            if choice.message is None:
                raise ProviderResponseError(
                    provider="openrouter",
                    endpoint=base_url,
                    details=f"Missing message in choice (finish_reason={finish_reason})",
                )
            content = choice.message.content
            if not content:
                raise ProviderEmptyContentError(
                    provider="openrouter",
                    endpoint=base_url,
                    details=f"Empty content in response (finish_reason={finish_reason})",
                )
            return content
        except openai.AuthenticationError as exc:
            raise ProviderAuthError(
                provider="openrouter", endpoint=base_url, details=str(exc)
            ) from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError(
                provider="openrouter", endpoint=base_url, details=str(exc)
            ) from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError(
                provider="openrouter", endpoint=base_url, details=str(exc)
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderConnectionError(
                provider="openrouter", endpoint=base_url, details=str(exc)
            ) from exc
        except openai.APIError as exc:
            raise ProviderResponseError(
                provider="openrouter", endpoint=base_url, details=str(exc)
            ) from exc
