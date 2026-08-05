import asyncio

import openai

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    create_chat_completion_json_fallback,
    raise_if_choice_errored,
    raise_if_truncated,
)

DEFAULT_MODEL = "qwen/qwen3.5-flash"
DEFAULT_BASE_URL = "https://api.atlascloud.ai/v1"
MAX_TOKENS_CEILING: int = 8192

ClientCacheKey = tuple[str, str | None, float, int]


class AtlasCloudProvider(ChatProvider):
    def __init__(self) -> None:
        self._clients: dict[ClientCacheKey, openai.AsyncOpenAI] = {}
        self._client_lock = asyncio.Lock()

    async def get_client(self, config: ProviderConfig) -> openai.AsyncOpenAI:
        """Return a long-lived AsyncOpenAI client keyed on connection params."""
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
                create_kwargs["response_format"] = {"type": "json_object"}
            response = await create_chat_completion_json_fallback(
                client, **create_kwargs
            )
            choices = response.choices
            if not choices:
                raise ProviderResponseError(
                    provider="atlascloud",
                    endpoint=base_url,
                    details=f"No choices in response (id={response.id})",
                )
            choice = choices[0]
            finish_reason = choice.finish_reason or "unknown"
            if choice.message is None:
                raise ProviderResponseError(
                    provider="atlascloud",
                    endpoint=base_url,
                    details=f"Missing message in choice (finish_reason={finish_reason})",
                )
            content = choice.message.content
            if config.reject_truncated:
                raise_if_truncated("atlascloud", base_url, finish_reason, content or "")
            if not content:
                raise ProviderEmptyContentError(
                    provider="atlascloud",
                    endpoint=base_url,
                    details=f"Empty content in response (finish_reason={finish_reason})",
                )
            raise_if_choice_errored("atlascloud", base_url, choice, content)
        except openai.AuthenticationError as exc:
            raise ProviderAuthError(
                provider="atlascloud", endpoint=base_url, details=str(exc)
            ) from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError(
                provider="atlascloud", endpoint=base_url, details=str(exc)
            ) from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError(
                provider="atlascloud", endpoint=base_url, details=str(exc)
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderConnectionError(
                provider="atlascloud", endpoint=base_url, details=str(exc)
            ) from exc
        except openai.APIError as exc:
            raise ProviderResponseError(
                provider="atlascloud", endpoint=base_url, details=str(exc)
            ) from exc
        else:
            return content
