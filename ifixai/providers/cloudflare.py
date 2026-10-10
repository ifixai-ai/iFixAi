"""Cloudflare AI Gateway, reached through its OpenAI-compatible endpoint.

One Cloudflare API token fronts Workers AI and third-party vendors. The model is
a ``provider/model`` slug such as ``openai/gpt-4.1-mini``, which for Workers AI
reads ``workers-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast``. The account and the
gateway are part of the URL, so they are read from ``CLOUDFLARE_ACCOUNT_ID`` and
``CLOUDFLARE_GATEWAY_ID`` unless an endpoint is given.
https://developers.cloudflare.com/ai-gateway/usage/chat-completion/
"""

import asyncio
import os
import re
from collections.abc import Mapping
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
from ifixai.providers.client_pool import close_cached_clients

PROVIDER_NAME = "cloudflare"
# Served by Workers AI on the free plan, so a new account can run it without
# prepaid credits; third-party models bill Unified Billing credits or a stored
# provider key. Written with the ``workers-ai/`` prefix because that is the form
# Cloudflare documents for this endpoint; the bare ``@cf/...`` id is also accepted.
DEFAULT_MODEL = "workers-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast"
# The gateway host, not the api.cloudflare.com REST route: a token issued for AI
# Gateway is accepted here and answered 401 there, which wants a token with the
# Workers AI permission instead.
GATEWAY_BASE_URL_TEMPLATE = (
    "https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/compat"
)
CREDENTIAL_ENV_VAR = "CLOUDFLARE_API_TOKEN"
ACCOUNT_ID_ENV_VAR = "CLOUDFLARE_ACCOUNT_ID"
GATEWAY_ID_ENV_VAR = "CLOUDFLARE_GATEWAY_ID"
ACCOUNT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
# One URL path segment. Case is kept: the gateway host tells "default" from "DEFAULT".
GATEWAY_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
DEFAULT_GATEWAY_ID = "default"
# Where Cloudflare's authentication page says the token goes on this host. Its
# SDK examples for this endpoint send the token as the API key instead, so the
# token travels both ways and either reading of the documentation is honoured.
GATEWAY_AUTHORIZATION_HEADER = "cf-aig-authorization"
SKIP_CACHE_HEADER = "cf-aig-skip-cache"
# AI Gateway's own code for "no gateway with this ID in the account".
GATEWAY_NOT_CONFIGURED_CODE = 2001
# Workers AI's code for "the account's free daily allocation is used up".
ALLOCATION_EXHAUSTED_CODE = 4006
# Hard ceiling on max_tokens for AI Gateway calls. Per-call ``config.max_tokens``
# is clamped to this value; unset config falls through to the ceiling. Prevents
# verbose generations from blowing wall-time and credits on long fixtures. Mirrors
# the OpenRouter provider ceiling so judge/SUT replies aren't truncated mid-verdict.
MAX_TOKENS_CEILING: int = 8192

ACCOUNT_ID_MISSING = (
    f"{ACCOUNT_ID_ENV_VAR} is not set. The account ID is part of the gateway URL: "
    f"set {ACCOUNT_ID_ENV_VAR} to the Account ID shown in the Cloudflare dashboard, "
    "or pass the full base URL with --endpoint"
)
ACCOUNT_ID_MALFORMED = (
    f"{ACCOUNT_ID_ENV_VAR} is not a Cloudflare account ID. It must be the 32 "
    "hexadecimal characters shown as Account ID in the dashboard; a gateway name, "
    "a zone ID or an API token will not work"
)
GATEWAY_ID_MALFORMED = (
    f"{GATEWAY_ID_ENV_VAR} is not a gateway ID. It must be the gateway's name as "
    "shown in the Cloudflare dashboard: up to 64 letters, digits, hyphens or "
    "underscores"
)
GATEWAY_NOT_FOUND_HINT = (
    "No AI Gateway with this ID exists in the account. Create it in the Cloudflare "
    f"dashboard, or set {GATEWAY_ID_ENV_VAR} to an existing gateway; the ID is "
    "case-sensitive."
)
# `insufficient_quota` is the phrase the fail-fast check keys on for a spent
# account; Cloudflare's own wording carries none of the phrases it looks for.
ALLOCATION_EXHAUSTED_HINT = (
    "The account's free daily Workers AI allocation is spent (insufficient_quota). "
    "It is a per-day allocation, so no retry in this run can succeed: wait for it "
    "to return, move the account to a paid Workers plan, or use a model billed "
    "through Unified Billing."
)
# A made-up token, no token and a valid token against another account were all
# seen to return one identical 401 body. The permission is the one Cloudflare
# documents for an authenticated gateway.
AUTHENTICATION_HINT = (
    f"Cloudflare reports three causes this one way: {CREDENTIAL_ENV_VAR} is not an "
    "active API token, the token lacks the AI Gateway > Run permission, or the "
    f"account ID ({ACCOUNT_ID_ENV_VAR}, or the one in --endpoint) is not the "
    "account the token belongs to."
)

# Checked in order, most specific first: a timeout is also a connection error,
# and the 429 class is also a status error.
SDK_ERROR_TRANSLATIONS: dict[type[openai.APIError], type[ProviderError]] = {
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


class CloudflareAIGatewayProvider(ChatProvider):
    def __init__(self) -> None:
        self.clients: dict[ClientCacheKey, openai.AsyncOpenAI] = {}
        self.client_lock = asyncio.Lock()

    async def get_client(self, connection: GatewayConnection) -> openai.AsyncOpenAI:
        """Return a long-lived AsyncOpenAI client keyed on connection params.

        Caching by (api_key, base_url, timeout, max_retries) lets the
        underlying httpx pool reuse TCP/TLS across LLM calls instead of
        paying a handshake per request.
        """
        key: ClientCacheKey = tuple(connection.values())
        cached = self.clients.get(key)
        if cached is not None:
            return cached
        async with self.client_lock:
            cached = self.clients.get(key)
            if cached is not None:
                return cached
            client = build_client(connection)
            self.clients[key] = client
            return client

    async def aclose(self) -> None:
        await close_cached_clients(self.clients)

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        connection = resolve_connection(config, os.environ)
        endpoint = connection["base_url"]
        client = await self.get_client(connection)
        try:
            response = await create_chat_completion_json_fallback(
                client, **build_chat_request(messages, config)
            )
        except openai.APIError as exc:
            raise_for_sdk_error(endpoint, exc)
        return extract_reply(response, config, endpoint)


def resolve_connection(
    config: ProviderConfig, environ: Mapping[str, str]
) -> GatewayConnection:
    """Reduce a config and the environment to the settings a client is built from.

    The account and the gateway come from the environment because a judge is
    configured with a provider and a model only: it has no endpoint to carry them.
    """
    base_url = (config.endpoint or "").strip() or build_gateway_url(environ)
    return GatewayConnection(
        api_key=require_api_token(config.api_key, base_url),
        base_url=base_url,
        timeout=float(config.timeout),
        max_retries=config.max_retries,
    )


def build_gateway_url(environ: Mapping[str, str]) -> str:
    """Return the gateway's base URL for the account and gateway in the environment."""
    return GATEWAY_BASE_URL_TEMPLATE.format(
        account_id=require_account_id(environ.get(ACCOUNT_ID_ENV_VAR, "")),
        gateway_id=resolve_gateway_id(environ.get(GATEWAY_ID_ENV_VAR, "")),
    )


def require_account_id(raw_account_id: str) -> str:
    """Return the account ID in lower case, refusing a missing or malformed one.

    Lower case because the gateway host answers an upper-case account ID with
    the same 401 it gives a wrong one.
    """
    account_id = raw_account_id.strip().lower()
    if not account_id:
        refuse_setting(ACCOUNT_ID_MISSING)
    if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
        refuse_setting(ACCOUNT_ID_MALFORMED)
    return account_id


def resolve_gateway_id(raw_gateway_id: str) -> str:
    """Return the named gateway, or the default gateway when none is named.

    The ID becomes a URL path segment, so anything that is not one is refused
    here instead of being sent as part of a different path.
    """
    gateway_id = raw_gateway_id.strip()
    if not gateway_id:
        return DEFAULT_GATEWAY_ID
    if not GATEWAY_ID_PATTERN.fullmatch(gateway_id):
        refuse_setting(GATEWAY_ID_MALFORMED)
    return gateway_id


def refuse_setting(details: str) -> NoReturn:
    """Stop the run over an environment setting no request can succeed without.

    Raised as an auth error, not a connection error: a connection fault is
    transient by type, so a judge with a bad setting would lose every probe one
    at a time instead of stopping the run. The value is never echoed, because
    the usual mistake is pasting the token into the wrong variable.
    """
    raise ProviderAuthError(
        provider=PROVIDER_NAME, endpoint=GATEWAY_BASE_URL_TEMPLATE, details=details
    )


def require_api_token(raw_token: str, endpoint: str) -> str:
    """Return the token without surrounding whitespace, refusing a blank one.

    A pasted token often carries a trailing newline, which is an illegal header
    value: the request fails before it is sent and the transport error quotes
    the token. A blank token is refused here because the SDK reports it as a
    missing ``OPENAI_API_KEY``, which names the wrong variable.
    """
    api_token = raw_token.strip()
    if not api_token:
        raise ProviderAuthError(
            provider=PROVIDER_NAME,
            endpoint=endpoint,
            details=(
                "No API token was supplied (empty or whitespace only). "
                f"Set {CREDENTIAL_ENV_VAR} or pass --api-key"
            ),
        )
    return api_token


def build_client(connection: GatewayConnection) -> openai.AsyncOpenAI:
    """Build an SDK client that stamps the gateway headers on every request."""
    return openai.AsyncOpenAI(
        api_key=connection["api_key"],
        base_url=connection["base_url"],
        timeout=connection["timeout"],
        max_retries=connection["max_retries"],
        default_headers=build_gateway_headers(connection["api_key"]),
    )


def build_gateway_headers(api_token: str) -> dict[str, str]:
    """Authenticate to the gateway and bypass its response cache.

    A gateway with caching on replays one stored answer to every identical
    request, which would make a repeated probe measure the cache instead of the
    model.
    """
    return {
        GATEWAY_AUTHORIZATION_HEADER: f"Bearer {api_token}",
        SKIP_CACHE_HEADER: "true",
    }


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
        # No `reasoning` suppression here, unlike OpenRouter. `reasoning` is
        # OpenRouter's own gateway extension and Cloudflare documents no
        # equivalent for this route. Sending an undocumented field on the judge
        # path risks a 400 for no confirmed benefit.
        request["response_format"] = JsonResponseFormat(type="json_object")
    return request


def resolve_max_tokens(requested: int | None) -> int:
    """Clamp the caller's token budget to the ceiling; unset takes the ceiling."""
    if requested is None:
        return MAX_TOKENS_CEILING
    return min(requested, MAX_TOKENS_CEILING)


def raise_for_sdk_error(endpoint: str, exc: openai.APIError) -> NoReturn:
    """Translate an OpenAI-SDK failure into the matching provider exception."""
    if isinstance(exc, openai.AuthenticationError):
        raise_for_rejected_access(endpoint, exc, AUTHENTICATION_HINT)
    if isinstance(exc, openai.RateLimitError) and is_allocation_exhausted(exc):
        raise_for_exhausted_allocation(endpoint, exc)
    for sdk_error, provider_error in SDK_ERROR_TRANSLATIONS.items():
        if isinstance(exc, sdk_error):
            raise provider_error(
                provider=PROVIDER_NAME, endpoint=endpoint, details=str(exc)
            ) from exc
    if not isinstance(exc, openai.APIStatusError):
        raise ProviderResponseError(
            provider=PROVIDER_NAME, endpoint=endpoint, details=str(exc)
        ) from exc
    if is_missing_gateway(exc):
        raise_for_rejected_access(endpoint, exc, GATEWAY_NOT_FOUND_HINT)
    if exc.status_code == HTTPStatus.PAYMENT_REQUIRED:
        raise_for_payment_required(endpoint, exc)
    # 408 and 5xx are the gateway or the model behind it having a moment, so they
    # are split off as a transient overload; everything else stays a response error.
    raise_for_http_status(PROVIDER_NAME, endpoint, exc)


def raise_for_rejected_access(
    endpoint: str, exc: openai.APIError, hint: str
) -> NoReturn:
    """Report a rejected token, account or gateway as fatal, with what to check.

    None of them clears on a retry, so each has to stop the run with Cloudflare's
    own message kept and the likely causes named beside it.
    """
    raise ProviderAuthError(
        provider=PROVIDER_NAME, endpoint=endpoint, details=f"{exc}. {hint}"
    ) from exc


def is_missing_gateway(exc: openai.APIStatusError) -> bool:
    """True when AI Gateway says the account has no gateway with this ID.

    It answers 400 for that, a status that would otherwise read as one bad
    request and fail the same way on every probe. The SDK keeps only the body's
    ``error`` member, which the gateway sends as a list of coded faults.
    """
    faults = exc.body
    if exc.status_code != HTTPStatus.BAD_REQUEST or not isinstance(faults, list):
        return False
    return any(
        isinstance(fault, dict) and fault.get("code") == GATEWAY_NOT_CONFIGURED_CODE
        for fault in faults
    )


def is_allocation_exhausted(exc: openai.APIStatusError) -> bool:
    """True when Workers AI says the account's free daily allocation is used up.

    Unlike the gateway's own envelope, a Workers AI fault has no ``error``
    member, so the SDK keeps the whole body.
    """
    body = exc.body
    return (
        isinstance(body, dict) and body.get("internalCode") == ALLOCATION_EXHAUSTED_CODE
    )


def raise_for_exhausted_allocation(
    endpoint: str, exc: openai.APIStatusError
) -> NoReturn:
    """Report a spent daily allocation as a rate limit that will not clear.

    It arrives as a 429, which reads as a throttle that lifts in a moment. Left
    that way a run fails every remaining probe one at a time. It stays a rate
    limit error, as the fail-fast check expects of a spent account, with the
    phrase that check looks for.
    """
    raise ProviderRateLimitError(
        provider=PROVIDER_NAME,
        endpoint=endpoint,
        details=f"{exc}. {ALLOCATION_EXHAUSTED_HINT}",
    ) from exc


def raise_for_payment_required(endpoint: str, exc: openai.APIStatusError) -> NoReturn:
    """Report an empty Unified Billing balance as non-retryable.

    The gateway words the 402 body two different ways depending on the model,
    and one of them matches none of the phrases the fail-fast check looks for.
    The status's own reason phrase goes into the detail: it is what the
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
    """Return the reply text, rejecting aborted, cut-off and empty generations."""
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
    # An upstream abort is checked first: it can arrive with no text at all, and
    # reading that as an empty answer would void the inspection instead of the probe.
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
