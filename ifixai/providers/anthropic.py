import asyncio

import anthropic

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
from ifixai.providers.schemas import MessageSplit

DEFAULT_MODEL = "claude-sonnet-4-6"

INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0

ClientCacheKey = tuple[str | None, str | None, float]


class AnthropicProvider(ChatProvider):

    def __init__(self) -> None:
        self._clients: dict[ClientCacheKey, anthropic.AsyncAnthropic] = {}
        self._client_lock = asyncio.Lock()

    async def get_client(self, config: ProviderConfig) -> anthropic.AsyncAnthropic:
        """Return a long-lived AsyncAnthropic client keyed on connection params.

        SDK-internal retries stay disabled (max_retries=0) because we run our
        own exponential backoff loop in send_message. Sharing one client per
        (endpoint, api_key, timeout) reuses the underlying httpx pool across
        the run instead of paying TLS setup per LLM call.
        """
        key: ClientCacheKey = (
            config.endpoint,
            config.api_key,
            float(config.timeout),
        )
        cached = self._clients.get(key)
        if cached is not None:
            return cached
        async with self._client_lock:
            cached = self._clients.get(key)
            if cached is not None:
                return cached
            client = anthropic.AsyncAnthropic(
                api_key=config.api_key,
                base_url=config.endpoint,
                timeout=float(config.timeout),
                max_retries=0,
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
        client = await self.get_client(config)

        model = config.model or DEFAULT_MODEL
        endpoint = config.endpoint or "https://api.anthropic.com"

        split = _split_system_and_messages(messages)
        system_text = split["system_text"]
        formatted_messages = split["messages"]

        attempts = config.max_retries + 1
        backoff = INITIAL_BACKOFF_SECONDS

        for attempt in range(attempts):
            try:
                kwargs: dict = {
                    "model": model,
                    "max_tokens": config.max_tokens or 4096,
                    "messages": formatted_messages,
                    "temperature": config.temperature,
                }
                if system_text:
                    kwargs["system"] = system_text
                # Anthropic API does not expose a `seed` parameter. Seed is
                # tracked on ProviderConfig for manifest reproducibility but
                # the underlying SDK call cannot pin it.

                response = await client.messages.create(**kwargs)

                content_blocks = response.content
                if not content_blocks:
                    raise ProviderEmptyContentError(
                        provider="anthropic",
                        endpoint=endpoint,
                        details="Empty content in response",
                    )

                text_parts = [
                    block.text for block in content_blocks if block.type == "text"
                ]
                if not text_parts:
                    raise ProviderEmptyContentError(
                        provider="anthropic",
                        endpoint=endpoint,
                        details="No text blocks in response",
                    )
                return "\n".join(text_parts)

            except anthropic.AuthenticationError as exc:
                raise ProviderAuthError(
                    provider="anthropic",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except anthropic.RateLimitError as exc:
                if attempt < attempts - 1:
                    await asyncio.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER
                    continue
                raise ProviderRateLimitError(
                    provider="anthropic",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except anthropic.APIConnectionError as exc:
                raise ProviderConnectionError(
                    provider="anthropic",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except anthropic.APITimeoutError as exc:
                raise ProviderTimeoutError(
                    provider="anthropic",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except anthropic.APIError as exc:
                raise ProviderResponseError(
                    provider="anthropic",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc

        raise ProviderRateLimitError(
            provider="anthropic",
            endpoint=endpoint,
            details="Exhausted all retry attempts",
        )


def _split_system_and_messages(
    messages: list[ChatMessage],
) -> MessageSplit:
    system_text = ""
    conversation: list[dict[str, str]] = []

    for msg in messages:
        if msg.role == "system":
            system_text = msg.content
        else:
            conversation.append({"role": msg.role, "content": msg.content})

    return MessageSplit(system_text=system_text, messages=conversation)
