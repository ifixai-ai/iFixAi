import asyncio

import openai

from ifixai.providers.base import (
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from ifixai.core.types import ChatMessage, ProviderConfig

DEFAULT_API_VERSION = "2024-10-21"

ClientCacheKey = tuple[str, str | None, float, int]


class AzureOpenAIProvider(ChatProvider):

    def __init__(
        self,
        api_version: str = DEFAULT_API_VERSION,
        azure_ad_token: str | None = None,
    ) -> None:
        self.api_version = api_version
        self.azure_ad_token = azure_ad_token
        self._clients: dict[ClientCacheKey, openai.AsyncAzureOpenAI] = {}
        self._client_lock = asyncio.Lock()

    async def get_client(self, config: ProviderConfig) -> openai.AsyncAzureOpenAI:
        """Return a long-lived AsyncAzureOpenAI client keyed on connection params.

        Caching one client per (endpoint, credential, timeout, max_retries)
        keeps the underlying httpx pool warm across the run instead of
        re-handshaking TLS for every LLM call.
        """
        credential = self.azure_ad_token or config.api_key
        key: ClientCacheKey = (
            config.endpoint or "",
            credential,
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
            client_kwargs: dict = {
                "azure_endpoint": config.endpoint,
                "api_version": self.api_version,
                "timeout": float(config.timeout),
                "max_retries": config.max_retries,
            }
            if self.azure_ad_token:
                client_kwargs["azure_ad_token"] = self.azure_ad_token
            else:
                client_kwargs["api_key"] = config.api_key
            client = openai.AsyncAzureOpenAI(**client_kwargs)
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
        if not config.endpoint:
            raise ProviderConnectionError(
                provider="azure",
                endpoint="",
                details=(
                    "Azure endpoint is required. "
                    "Set config.endpoint to your Azure OpenAI resource URL."
                ),
            )

        if not config.model:
            raise ProviderResponseError(
                provider="azure",
                endpoint=config.endpoint,
                details=(
                    "Azure deployment name (model) is required. "
                    "Set config.model to your Azure OpenAI deployment name."
                ),
            )

        client = await self.get_client(config)

        formatted_messages = [{"role": m.role, "content": m.content} for m in messages]

        params: dict[str, object] = {
            "model": config.model,
            "messages": formatted_messages,
            "temperature": config.temperature,
        }
        if config.seed is not None:
            params["seed"] = config.seed
        if config.max_tokens is not None:
            params["max_tokens"] = config.max_tokens
        try:
            response = await client.chat.completions.create(**params)  # type: ignore[arg-type]

            choices = response.choices
            if not choices:
                raise ProviderResponseError(
                    provider="azure",
                    endpoint=config.endpoint,
                    details=f"No choices in response (id={response.id})",
                )
            choice = choices[0]
            finish_reason = choice.finish_reason or "unknown"
            if choice.message is None:
                raise ProviderResponseError(
                    provider="azure",
                    endpoint=config.endpoint,
                    details=f"Missing message in choice (finish_reason={finish_reason})",
                )
            content = choice.message.content
            if not content:
                raise ProviderEmptyContentError(
                    provider="azure",
                    endpoint=config.endpoint,
                    details=f"Empty content in response (finish_reason={finish_reason})",
                )
            return content

        except openai.AuthenticationError as exc:
            raise ProviderAuthError(
                provider="azure",
                endpoint=config.endpoint,
                details=str(exc),
            ) from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError(
                provider="azure",
                endpoint=config.endpoint,
                details=str(exc),
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderConnectionError(
                provider="azure",
                endpoint=config.endpoint,
                details=str(exc),
            ) from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError(
                provider="azure",
                endpoint=config.endpoint,
                details=str(exc),
            ) from exc
        except openai.APIError as exc:
            raise ProviderResponseError(
                provider="azure",
                endpoint=config.endpoint,
                details=str(exc),
            ) from exc
