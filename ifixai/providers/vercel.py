"""Vercel AI Gateway, reached through its OpenAI-compatible chat-completions API.

One key fronts every vendor; the model is a ``creator/model`` slug such as
``anthropic/claude-haiku-4.5``.
https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions
"""

import asyncio
from http import HTTPStatus
from typing import NoReturn

import openai
from openai.types.chat import ChatCompletion
from typing_extensions import NotRequired, TypedDict

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    create_chat_completion_json_fallback,
    raise_for_http_status,
    raise_if_choice_errored,
    raise_if_truncated,
)

PROVIDER_NAME = "vercel"
DEFAULT_MODEL = "openai/gpt-4o-mini"
DEFAULT_BASE_URL = "https://ai-gateway.vercel.sh/v1"
# Hard ceiling on max_tokens for AI Gateway calls. Per-call ``config.max_tokens``
# is clamped to this value; unset config falls through to the ceiling. Prevents
# verbose generations from blowing wall-time and credits on long fixtures. Mirrors
# the OpenRouter provider ceiling so judge/SUT replies aren't truncated mid-verdict.
MAX_TOKENS_CEILING: int = 8192

# Checked in order, most specific first: a timeout is also a connection error,
# and the 401 and 429 classes are also status errors.
SDK_ERROR_TRANSLATIONS: dict[type[openai.APIError], type[ProviderError]] = {
    openai.AuthenticationError: ProviderAuthError,
    openai.RateLimitError: ProviderRateLimitError,
    openai.APITimeoutError: ProviderTimeoutError,
    openai.APIConnectionError: ProviderConnectionError,
}

ClientCacheKey = tuple[object, ...]


class GatewayConnection(TypedDict):
    """Everything that distinguishes one SDK client from another."""

    api_key: str
    base_url: str
    timeout: float
    max_retries: int


class GatewayMessage(TypedDict):
    role: str
    content: str


class JsonResponseFormat(TypedDict):
    type: str


class GatewayChatRequest(TypedDict):
    """Body of one chat-completions call; optional keys are sent only when set."""

    model: str
    messages: list[GatewayMessage]
    max_tokens: int
    temperature: float
    seed: NotRequired[int]
    response_format: NotRequired[JsonResponseFormat]


class VercelAIGatewayProvider(ChatProvider):
    def __init__(self) -> None:
        self.clients: dict[ClientCacheKey, openai.AsyncOpenAI] = {}
        self.client_lock = asyncio.Lock()

    async def get_client(self, config: ProviderConfig) -> openai.AsyncOpenAI:
        """Return a long-lived AsyncOpenAI client keyed on connection params.

        Caching by (api_key, base_url, timeout, max_retries) lets the
        underlying httpx pool reuse TCP/TLS across LLM calls instead of
        paying a handshake per request.
        """
        connection = resolve_connection(config)
        key: ClientCacheKey = tuple(connection.values())
        cached = self.clients.get(key)
        if cached is not None:
            return cached
        async with self.client_lock:
            cached = self.clients.get(key)
            if cached is not None:
                return cached
            client = openai.AsyncOpenAI(**connection)
            self.clients[key] = client
            return client

    async def aclose(self) -> None:
        for client in self.clients.values():
            await client.close()
        self.clients.clear()

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        endpoint = config.endpoint or DEFAULT_BASE_URL
        client = await self.get_client(config)
        try:
            response = await create_chat_completion_json_fallback(
                client, **build_chat_request(messages, config)
            )
        except openai.APIError as exc:
            raise_for_sdk_error(endpoint, exc)
        return extract_reply(response, config, endpoint)


def resolve_connection(config: ProviderConfig) -> GatewayConnection:
    """Reduce a config to the settings an SDK client is built from."""
    base_url = config.endpoint or DEFAULT_BASE_URL
    return GatewayConnection(
        api_key=require_api_key(config.api_key, base_url),
        base_url=base_url,
        timeout=float(config.timeout),
        max_retries=config.max_retries,
    )


def require_api_key(raw_key: str, endpoint: str) -> str:
    """Return the key without surrounding whitespace, refusing a blank one.

    A pasted key often carries a trailing newline, which is an illegal header
    value: the request fails before it is sent and the transport error quotes
    the key. A blank key is refused here because the SDK reports it as a
    missing ``OPENAI_API_KEY``, which names the wrong variable.
    """
    api_key = raw_key.strip()
    if not api_key:
        raise ProviderAuthError(
            provider=PROVIDER_NAME,
            endpoint=endpoint,
            details="No API key was supplied (empty or whitespace only)",
        )
    return api_key


def build_chat_request(
    messages: list[ChatMessage], config: ProviderConfig
) -> GatewayChatRequest:
    """Assemble the chat-completions body for one call."""
    request = GatewayChatRequest(
        model=config.model or DEFAULT_MODEL,
        messages=[
            GatewayMessage(role=message.role, content=message.content)
            for message in messages
        ],
        max_tokens=resolve_max_tokens(config.max_tokens),
        temperature=config.temperature,
    )
    if config.seed is not None:
        request["seed"] = config.seed
    if config.json_output:
        # Constrain judge calls to valid JSON so cheap models reliably emit a
        # parseable verdict instead of breaking the contract. Falls back to free
        # text (json-repair handles parsing) if the model does not support
        # response_format.
        #
        # No `reasoning` suppression here, unlike OpenRouter. The gateway documents
        # the same extension, but its catalog lists `reasoning` as unsupported for
        # non-reasoning models (this adapter's default among them) and does not say
        # whether such a request is ignored or rejected. A rejection would cost a
        # second round trip on every judge call.
        request["response_format"] = JsonResponseFormat(type="json_object")
    return request


def resolve_max_tokens(requested: int | None) -> int:
    """Clamp the caller's token budget to the ceiling; unset takes the ceiling."""
    if requested is None:
        return MAX_TOKENS_CEILING
    return min(requested, MAX_TOKENS_CEILING)


def raise_for_sdk_error(endpoint: str, exc: openai.APIError) -> NoReturn:
    """Translate an OpenAI-SDK failure into the matching provider exception."""
    for sdk_error, provider_error in SDK_ERROR_TRANSLATIONS.items():
        if isinstance(exc, sdk_error):
            raise provider_error(
                provider=PROVIDER_NAME, endpoint=endpoint, details=str(exc)
            ) from exc
    if not isinstance(exc, openai.APIStatusError):
        raise ProviderResponseError(
            provider=PROVIDER_NAME, endpoint=endpoint, details=str(exc)
        ) from exc
    if exc.status_code == HTTPStatus.PAYMENT_REQUIRED:
        raise_for_payment_required(endpoint, exc)
    # 408 and 5xx are the gateway or the model behind it having a moment, so they
    # are split off as a transient overload; everything else stays a response error.
    raise_for_http_status(PROVIDER_NAME, endpoint, exc)


def raise_for_payment_required(endpoint: str, exc: openai.APIStatusError) -> NoReturn:
    """Report an empty credit balance or a spent budget as non-retryable.

    Vercel documents the 402 status for both but not the wording of the body,
    so the status's own reason phrase goes into the detail: it is what the
    fail-fast check and the operator-facing hint both key on.
    """
    status = HTTPStatus.PAYMENT_REQUIRED
    raise ProviderResponseError(
        provider=PROVIDER_NAME,
        endpoint=endpoint,
        details=f"HTTP {status.value} {status.phrase}: {exc}",
    ) from exc


def extract_reply(
    response: ChatCompletion, config: ProviderConfig, endpoint: str
) -> str:
    """Return the reply text, rejecting empty, cut-off and aborted generations."""
    if not response.choices:
        raise ProviderResponseError(
            provider=PROVIDER_NAME,
            endpoint=endpoint,
            details=f"No choices in response (id={response.id})",
        )
    choice = response.choices[0]
    finish_reason = choice.finish_reason or "unknown"
    if choice.message is None:
        raise ProviderResponseError(
            provider=PROVIDER_NAME,
            endpoint=endpoint,
            details=f"Missing message in choice (finish_reason={finish_reason})",
        )
    content = choice.message.content
    # An upstream abort can arrive with no text, so it is checked before the
    # empty-reply check, which would void the inspection instead of the probe.
    raise_if_choice_errored(PROVIDER_NAME, endpoint, choice, content or "")
    if config.reject_truncated:
        raise_if_truncated(PROVIDER_NAME, endpoint, finish_reason, content or "")
    if not content:
        raise ProviderEmptyContentError(
            provider=PROVIDER_NAME,
            endpoint=endpoint,
            details=f"Empty content in response (finish_reason={finish_reason})",
        )
    return content
