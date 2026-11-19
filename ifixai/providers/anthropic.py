import asyncio
import logging

import anthropic

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.evaluation.run_stop import count_request
from ifixai.providers.base import (
    RETRYABLE_HTTP_STATUS_CODES,
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    raise_for_http_status,
    raise_if_truncated,
)
from ifixai.providers.client_pool import close_cached_clients
from ifixai.providers.schemas import MessageSplit

DEFAULT_MODEL = "claude-sonnet-4-6"

INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0

ClientCacheKey = tuple[str | None, str | None, float]

_logger = logging.getLogger(__name__)

# Endpoint/model pairs whose API rejected temperature. Process-wide so the SUT,
# judge and connection check share learned support only for the same API.
_NO_TEMPERATURE_MODELS: set[tuple[str, str]] = set()
# The API root each configured endpoint sends to (None: wherever the SDK sends by
# default), kept when its client is made so a provider that sent nothing can look.
_API_ROOTS: dict[str | None, str] = {}
# (API root, model, role) already warned about. The agent and the judge can share an
# endpoint and model, so each role that loses temperature is named once.
_DEFAULT_TEMPERATURE_WARNED: set[tuple[str, str, str]] = set()


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
            _API_ROOTS[config.endpoint] = _api_root(client)
            return client

    async def aclose(self) -> None:
        await close_cached_clients(self._clients)

    def accepts_temperature(self, model: str | None, endpoint: str | None) -> bool:
        # As far as this run knows: a pair joins _NO_TEMPERATURE_MODELS on its first 400.
        root = _API_ROOTS.get(endpoint)
        return root is None or (root, model or DEFAULT_MODEL) not in _NO_TEMPERATURE_MODELS

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
            count_request()
            try:
                kwargs: dict = {
                    "model": model,
                    "max_tokens": config.max_tokens or 4096,
                    "messages": formatted_messages,
                }
                if system_text:
                    kwargs["system"] = system_text
                # Anthropic API does not expose a `seed` parameter. Seed is
                # tracked on ProviderConfig for manifest reproducibility but
                # the underlying SDK call cannot pin it.

                response = await _create_message(client, kwargs, config.temperature)
                _warn_once_at_default_temperature(_api_root(client), model, config.role)

                content_blocks = response.content
                text_parts = [
                    block.text
                    for block in (content_blocks or [])
                    if block.type == "text"
                ]
                if config.reject_truncated:
                    raise_if_truncated(
                        "anthropic",
                        endpoint,
                        response.stop_reason or "unknown",
                        "\n".join(text_parts),
                    )
                if not content_blocks:
                    raise ProviderEmptyContentError(
                        provider="anthropic",
                        endpoint=endpoint,
                        details="Empty content in response",
                    )

                if not any(text_parts):
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
            except anthropic.APITimeoutError as exc:
                raise ProviderTimeoutError(
                    provider="anthropic",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except anthropic.APIConnectionError as exc:
                if attempt < attempts - 1:
                    await asyncio.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER
                    continue
                raise ProviderConnectionError(
                    provider="anthropic",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except anthropic.APIError as exc:
                if (getattr(exc, "status_code", None) in RETRYABLE_HTTP_STATUS_CODES
                        or getattr(exc, "status_code", None) == 529) and attempt < attempts - 1:
                    await asyncio.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER
                    continue
                raise_for_http_status("anthropic", endpoint, exc)

        raise ProviderRateLimitError(
            provider="anthropic",
            endpoint=endpoint,
            details="Exhausted all retry attempts",
        )


async def _create_message(
    client: anthropic.AsyncAnthropic,
    kwargs: dict,
    temperature: float,
) -> anthropic.types.Message:
    # SDK 1.x dropped the temperature kwarg, so it rides in extra_body. Newer
    # models 400 on any temperature: retry once without it and remember the model.
    model = kwargs["model"]
    capability_key = (_api_root(client), model)
    if capability_key not in _NO_TEMPERATURE_MODELS:
        try:
            return await client.messages.create(
                **kwargs, extra_body={"temperature": temperature}
            )
        except anthropic.BadRequestError as exc:
            # Outside 0..1 the 400 is our bad value, not a model that refuses temperature.
            if not 0 <= temperature <= 1 or "temperature" not in str(exc).lower():
                raise
            _NO_TEMPERATURE_MODELS.add(capability_key)
    return await client.messages.create(**kwargs)


def _warn_once_at_default_temperature(root: str, model: str, role: str) -> None:
    warned_key = (root, model, role)
    if (root, model) not in _NO_TEMPERATURE_MODELS or warned_key in _DEFAULT_TEMPERATURE_WARNED:
        return
    _DEFAULT_TEMPERATURE_WARNED.add(warned_key)
    _logger.warning(
        "%s model %s rejects temperature; the %s runs at the model's default temperature",
        role,
        model,
        role,
    )


def _api_root(client: anthropic.AsyncAnthropic) -> str:
    return str(client.base_url).rstrip("/")


def _split_system_and_messages(
    messages: list[ChatMessage],
) -> MessageSplit:
    system_parts: list[str] = []
    conversation: list[dict[str, str]] = []

    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
        else:
            conversation.append({"role": msg.role, "content": msg.content})

    return MessageSplit(system_text="\n".join(system_parts), messages=conversation)
