"""Cloudflare AI Gateway adapter: wire contract, URL resolution and error mapping.

The wire tests drive the real OpenAI SDK against a loopback stand-in for the
gateway and replay the bodies the live gateway host returned on 2026-10-09, so
the mapping is pinned to what the SDK raises for real payloads rather than to
hand-built exceptions. The 429 and 5xx bodies are the exception: none could be
provoked, so they reuse the gateway's envelope with a message of their own, and
the mapping for them keys on the HTTP status alone.

References:
  https://developers.cloudflare.com/ai-gateway/usage/chat-completion/
  https://developers.cloudflare.com/ai-gateway/configuration/authentication/
  https://developers.cloudflare.com/ai-gateway/features/caching/
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, TypedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

pytest.importorskip("openai")

from ifixai.cli.main import ifixai_cli
from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ProviderAuthError,
    ProviderEmptyContentError,
    ProviderError,
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTruncatedError,
    friendly_provider_message,
    is_fatal_provider_error,
)
from ifixai.providers.cloudflare import (
    ACCOUNT_ID_ENV_VAR,
    DEFAULT_MODEL,
    GATEWAY_AUTHORIZATION_HEADER,
    GATEWAY_ID_ENV_VAR,
    MAX_TOKENS_CEILING,
    SKIP_CACHE_HEADER,
    CloudflareAIGatewayProvider,
    resolve_connection,
)

# Assembled at import time so the source holds no token-shaped literal for the
# repo's secret scanner (gitleaks, see .pre-commit-config.yaml) to flag.
SYNTHETIC_TOKEN = "cfut_" + "syntheticLoopback" * 3
ACCOUNT_ID = "0123456789abcdef" * 2
GATEWAY_BASE_URL = f"https://gateway.ai.cloudflare.com/v1/{ACCOUNT_ID}/default/compat"
BARE_WORKERS_AI_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
MESSAGES = [
    ChatMessage(role="system", content="You are a claims assistant."),
    ChatMessage(role="user", content="Reply with the single word: pong"),
]


def gateway_error(status: int, code: int, message: str) -> dict[str, Any]:
    """An error in the envelope the gateway host puts around its own faults."""
    return {
        "success": False,
        "result": [],
        "messages": [],
        "error": [{"code": code, "message": message}],
        "name": "AiGatewayError",
        "httpCode": status,
        "internalCode": code,
        "message": message,
        "description": message,
    }


# Captured: returned alike for a made-up token, for no token and for a valid
# token against an account it does not belong to.
UNAUTHORIZED_BODY = gateway_error(401, 2009, "Unauthorized")
# Captured: a gateway ID the account does not have.
GATEWAY_NOT_CONFIGURED_BODY = gateway_error(
    400, 2001, "Please configure AI Gateway in the Cloudflare dashboard"
)
# Captured: a third-party model on an account with no Unified Billing credits.
NO_CREDITS_BODY = gateway_error(
    402,
    2021,
    "Insufficient unified billing credits. Please add additional credits on the "
    "AI Gateway Cloudflare dashboard to continue using provider models.",
)
# Captured: the same condition, worded differently for other models. It matches
# none of the phrases the fail-fast path looks for.
NO_BALANCE_BODY = {
    "error": {
        "message": "Insufficient balance; add money to your gateway or use BYOK",
        "type": "gateway_error",
        "code": None,
    }
}
# Captured: a slug the gateway does not route.
MODEL_NOT_FOUND_BODY = gateway_error(
    400, 2008, "Model not found: nosuchvendor/some-model"
)
# Captured: a Workers AI id that does not exist.
WORKERS_AI_NO_SUCH_MODEL_BODY = {
    "name": "AiError",
    "internalCode": 5007,
    "httpCode": 400,
    "message": (
        "AiError: No such model: No such model @cf/meta/no-such-model-ifixai or "
        "task (2f92db78-f004-4e34-bc23-383bd05f33cb)"
    ),
    "description": "No such model @cf/meta/no-such-model-ifixai or task",
    "requestId": "2f92db78-f004-4e34-bc23-383bd05f33cb",
}
# Captured: Workers AI once the account's free daily allocation is used up.
ALLOCATION_EXHAUSTED_BODY = {
    "name": "AiError",
    "internalCode": 4006,
    "httpCode": 429,
    "message": (
        "AiError: AiError: you have used up your daily free allocation of 10,000 "
        "neurons, please upgrade to Cloudflare's Workers Paid plan if you would "
        "like to continue usage. (983e5e13-234a-48d2-abef-0eb6eb55b753)"
    ),
    "description": (
        "you have used up your daily free allocation of 10,000 neurons, please "
        "upgrade to Cloudflare's Workers Paid plan if you would like to continue "
        "usage."
    ),
    "requestId": "983e5e13-234a-48d2-abef-0eb6eb55b753",
}
# Captured: a Workers AI model the account's plan does not include.
PLAN_RESTRICTED_BODY = {
    "name": "AiError",
    "internalCode": 5035,
    "httpCode": 403,
    "message": (
        "AiError: Model @cf/moonshotai/kimi-k2.6 is not available on the Workers "
        "Free plan: Model @cf/moonshotai/kimi-k2.6 is not available on the Workers "
        "Free plan. Upgrade to access this model: "
        "https://dash.cloudflare.com/?to=/:account/workers/plans "
        "(7b4a747d-6fd1-48d3-8b83-2f84913eabef)"
    ),
    "description": (
        "Model @cf/moonshotai/kimi-k2.6 is not available on the Workers Free plan. "
        "Upgrade to access this model: "
        "https://dash.cloudflare.com/?to=/:account/workers/plans"
    ),
    "requestId": "7b4a747d-6fd1-48d3-8b83-2f84913eabef",
}


def uncaptured_error(status: int, message: str) -> dict[str, Any]:
    """The gateway's envelope for a fault that could not be provoked live.

    It carries no gateway code: Cloudflare's codes for these are not known, and
    the mapping keys on the HTTP status alone.
    """
    return {
        "success": False,
        "result": [],
        "messages": [],
        "name": "AiGatewayError",
        "httpCode": status,
        "message": message,
        "description": message,
    }


# Not captured: the default model accepts JSON mode. The wording names the
# parameter, which is the one thing the shared fallback retries on.
JSON_MODE_REJECTED_BODY = uncaptured_error(
    400, "response_format is not supported by this model"
)


class RecordedRequest(TypedDict):
    path: str
    authorization: str | None
    gateway_authorization: str | None
    skip_cache: str | None
    body: dict[str, Any]


class CannedReply(TypedDict):
    status: int
    payload: dict[str, Any]


class GatewayStub(ThreadingHTTPServer):
    """Loopback stand-in for the gateway.

    Replies are served in order and the last one repeats, so a single-reply
    stub answers every request the same way. Every request is recorded.
    """

    def __init__(self, replies: list[CannedReply]) -> None:
        super().__init__(("127.0.0.1", 0), GatewayStubHandler)
        self.replies = replies
        self.requests: list[RecordedRequest] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/v1/account/default/compat"

    def next_reply(self) -> CannedReply:
        return self.replies[min(len(self.requests), len(self.replies)) - 1]


class GatewayStubHandler(BaseHTTPRequestHandler):
    server: GatewayStub

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.server.requests.append(
            RecordedRequest(
                path=self.path,
                authorization=self.headers.get("Authorization"),
                gateway_authorization=self.headers.get(GATEWAY_AUTHORIZATION_HEADER),
                skip_cache=self.headers.get(SKIP_CACHE_HEADER),
                body=json.loads(self.rfile.read(length)),
            )
        )
        reply = self.server.next_reply()
        encoded = json.dumps(reply["payload"]).encode()
        self.send_response(reply["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args: object) -> None:
        """Keep the per-request access line out of the test output."""


@contextmanager
def serving(*replies: CannedReply) -> Iterator[GatewayStub]:
    gateway = GatewayStub(list(replies))
    worker = threading.Thread(
        target=gateway.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    worker.start()
    try:
        yield gateway
    finally:
        gateway.shutdown()
        gateway.server_close()
        worker.join(2)


def failing(status: int, payload: dict[str, Any]) -> CannedReply:
    return CannedReply(status=status, payload=payload)


def completing(
    content: str | None = "pong",
    finish_reason: str = "stop",
    **choice_extras: Any,
) -> CannedReply:
    """A completion in the shape the gateway returned for a Workers AI model."""
    choice = {
        "index": 0,
        "finish_reason": finish_reason,
        "logprobs": None,
        "message": {"role": "assistant", "content": content, "refusal": None},
        "stop_reason": None,
        **choice_extras,
    }
    return CannedReply(
        status=200,
        payload={
            "id": "id-1791555147718",
            "object": "chat.completion",
            "created": 1791555147,
            "model": DEFAULT_MODEL,
            "choices": [choice],
            "usage": {"prompt_tokens": 42, "completion_tokens": 2, "total_tokens": 44},
        },
    )


def offline_config(**overrides: Any) -> ProviderConfig:
    baseline = ProviderConfig(
        provider="cloudflare", api_key=SYNTHETIC_TOKEN, timeout=10, max_retries=0
    )
    return baseline.model_copy(update=overrides)


def config_for(gateway: GatewayStub, **overrides: Any) -> ProviderConfig:
    return offline_config(endpoint=gateway.base_url, **overrides)


async def send(config: ProviderConfig) -> str:
    provider = CloudflareAIGatewayProvider()
    try:
        return await provider.send_message(MESSAGES, config)
    finally:
        await provider.aclose()


def stub_sdk_client() -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(finish_reason="stop", error=None)]
    response.choices[0].message.content = "ok"
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    client.close = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def no_ambient_cloudflare_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own wrangler variables must never steer a test."""
    for variable in (ACCOUNT_ID_ENV_VAR, GATEWAY_ID_ENV_VAR, "CLOUDFLARE_API_TOKEN"):
        monkeypatch.delenv(variable, raising=False)


async def test_a_call_reaches_the_chat_completions_route_with_the_bearer_token() -> None:
    with serving(completing("pong")) as gateway:
        reply = await send(config_for(gateway, model="openai/gpt-4.1-mini"))

    assert reply == "pong"
    [request] = gateway.requests
    assert request["path"] == "/v1/account/default/compat/chat/completions"
    assert request["authorization"] == f"Bearer {SYNTHETIC_TOKEN}"
    assert request["body"]["model"] == "openai/gpt-4.1-mini"
    assert request["body"]["messages"] == [
        {"role": "system", "content": "You are a claims assistant."},
        {"role": "user", "content": "Reply with the single word: pong"},
    ]


async def test_the_token_is_sent_both_ways_cloudflare_documents() -> None:
    """Its SDK examples for this endpoint send the token as the API key; its
    authentication page says this host reads it from a header of its own."""
    with serving(completing()) as gateway:
        await send(config_for(gateway))

    [request] = gateway.requests
    assert request["authorization"] == f"Bearer {SYNTHETIC_TOKEN}"
    assert request["gateway_authorization"] == f"Bearer {SYNTHETIC_TOKEN}"


def test_the_default_model_is_written_the_way_cloudflare_documents_it() -> None:
    assert DEFAULT_MODEL == f"workers-ai/{BARE_WORKERS_AI_MODEL}"


async def test_every_call_bypasses_the_gateway_s_response_cache() -> None:
    """A gateway with caching on would answer a repeated probe from its cache."""
    with serving(completing()) as gateway:
        await send(config_for(gateway))

    assert gateway.requests[0]["skip_cache"] == "true"


async def test_a_bare_workers_ai_model_id_is_sent_unchanged() -> None:
    """The gateway also accepts the id without the prefix, so it is not rewritten."""
    with serving(completing()) as gateway:
        await send(config_for(gateway, model=BARE_WORKERS_AI_MODEL))

    assert gateway.requests[0]["body"]["model"] == BARE_WORKERS_AI_MODEL


async def test_an_unconfigured_call_sends_only_the_documented_defaults() -> None:
    with serving(completing()) as gateway:
        await send(config_for(gateway))

    [request] = gateway.requests
    assert request["body"] == {
        "model": DEFAULT_MODEL,
        "messages": request["body"]["messages"],
        "max_tokens": MAX_TOKENS_CEILING,
        "temperature": 0.0,
    }


async def test_the_seed_and_temperature_are_forwarded() -> None:
    with serving(completing()) as gateway:
        await send(config_for(gateway, seed=7, temperature=0.4))

    [request] = gateway.requests
    assert request["body"]["seed"] == 7
    assert request["body"]["temperature"] == 0.4


@pytest.mark.parametrize(
    ("requested", "sent"),
    [(None, MAX_TOKENS_CEILING), (256, 256), (50_000, MAX_TOKENS_CEILING)],
)
async def test_the_token_budget_never_exceeds_the_ceiling(
    requested: int | None, sent: int
) -> None:
    with serving(completing()) as gateway:
        await send(config_for(gateway, max_tokens=requested))

    assert gateway.requests[0]["body"]["max_tokens"] == sent


async def test_a_judge_call_asks_for_json_and_sends_no_gateway_extension() -> None:
    with serving(completing('{"verdict": "pass"}')) as gateway:
        reply = await send(config_for(gateway, json_output=True))

    assert reply == '{"verdict": "pass"}'
    [request] = gateway.requests
    assert request["body"]["response_format"] == {"type": "json_object"}
    assert "reasoning" not in request["body"]


async def test_a_model_without_json_mode_costs_one_retry_as_free_text() -> None:
    with serving(
        failing(400, JSON_MODE_REJECTED_BODY), completing('{"verdict": "pass"}')
    ) as gateway:
        reply = await send(config_for(gateway, json_output=True))

    assert reply == '{"verdict": "pass"}'
    assert len(gateway.requests) == 2
    assert "response_format" in gateway.requests[0]["body"]
    assert "response_format" not in gateway.requests[1]["body"]


@pytest.mark.parametrize(
    ("status", "body", "expected", "aborts_the_run"),
    [
        (401, UNAUTHORIZED_BODY, ProviderAuthError, True),
        (400, GATEWAY_NOT_CONFIGURED_BODY, ProviderAuthError, True),
        (402, NO_CREDITS_BODY, ProviderResponseError, True),
        (402, NO_BALANCE_BODY, ProviderResponseError, True),
        (403, PLAN_RESTRICTED_BODY, ProviderResponseError, True),
        (400, MODEL_NOT_FOUND_BODY, ProviderResponseError, False),
        (400, WORKERS_AI_NO_SUCH_MODEL_BODY, ProviderResponseError, False),
        (429, uncaptured_error(429, "Rate limited"), ProviderRateLimitError, False),
        (429, ALLOCATION_EXHAUSTED_BODY, ProviderRateLimitError, True),
        (408, uncaptured_error(408, "Upstream timed out"), ProviderOverloadedError, False),
        (500, uncaptured_error(500, "Internal error"), ProviderOverloadedError, False),
        (502, uncaptured_error(502, "Bad gateway"), ProviderOverloadedError, False),
        (503, uncaptured_error(503, "Service unavailable"), ProviderOverloadedError, False),
        (504, uncaptured_error(504, "Gateway timed out"), ProviderOverloadedError, False),
    ],
)
async def test_each_gateway_status_maps_to_its_provider_error(
    status: int,
    body: dict[str, Any],
    expected: type[ProviderError],
    aborts_the_run: bool,
) -> None:
    with serving(failing(status, body)) as gateway:
        with pytest.raises(ProviderError) as caught:
            await send(config_for(gateway))

    assert type(caught.value) is expected
    assert caught.value.provider == "cloudflare"
    assert caught.value.endpoint == gateway.base_url
    assert str(status) in caught.value.details
    assert is_fatal_provider_error(caught.value) is aborts_the_run


async def test_a_rejected_request_names_every_cause_cloudflare_reports_alike() -> None:
    """A made-up token, no token and a token on another account all come back as
    one bare "Unauthorized", so the error has to list what to check."""
    with serving(failing(401, UNAUTHORIZED_BODY)) as gateway:
        with pytest.raises(ProviderAuthError) as caught:
            await send(config_for(gateway))

    details = caught.value.details
    assert "Unauthorized" in details
    assert "CLOUDFLARE_API_TOKEN" in details
    assert "AI Gateway > Run" in details
    assert ACCOUNT_ID_ENV_VAR in details


async def test_a_gateway_the_account_does_not_have_stops_the_run() -> None:
    """Cloudflare answers 400 for it. Read as one bad request, a mistyped gateway
    ID would fail every probe the same way without ever stopping the run."""
    with serving(failing(400, GATEWAY_NOT_CONFIGURED_BODY)) as gateway:
        with pytest.raises(ProviderAuthError) as caught:
            await send(config_for(gateway))

    details = caught.value.details
    assert "Please configure AI Gateway" in details
    assert GATEWAY_ID_ENV_VAR in details
    assert "case-sensitive" in details
    assert is_fatal_provider_error(caught.value)


async def test_the_gateway_code_alone_is_not_read_as_a_missing_gateway() -> None:
    """Only the 400 that carries the code was seen to mean a missing gateway. The
    same code on an outage stays an outage: transient, and not a reason to stop."""
    outage = gateway_error(
        503, 2001, "Please configure AI Gateway in the Cloudflare dashboard"
    )
    with serving(failing(503, outage)) as gateway:
        with pytest.raises(ProviderOverloadedError) as caught:
            await send(config_for(gateway))

    assert not is_fatal_provider_error(caught.value)


async def test_a_spent_daily_allocation_stops_the_run_with_cloudflare_s_message() -> None:
    """It arrives as a 429. Read as a throttle, it would fail every remaining
    probe one at a time: the allocation does not return within a run."""
    with serving(failing(429, ALLOCATION_EXHAUSTED_BODY)) as gateway:
        with pytest.raises(ProviderRateLimitError) as caught:
            await send(config_for(gateway))

    assert "daily free allocation of 10,000 neurons" in caught.value.details
    assert "Unified Billing" in caught.value.details
    assert is_fatal_provider_error(caught.value)
    hint = friendly_provider_message(caught.value.details)
    assert hint is not None
    assert "quota" in hint


async def test_an_ordinary_rate_limit_from_workers_ai_is_still_a_throttle() -> None:
    """Only the allocation code means the account is spent."""
    throttled = {**ALLOCATION_EXHAUSTED_BODY, "internalCode": 3040}
    throttled["message"] = throttled["description"] = "Capacity temporarily exceeded"
    with serving(failing(429, throttled)) as gateway:
        with pytest.raises(ProviderRateLimitError) as caught:
            await send(config_for(gateway))

    assert not is_fatal_provider_error(caught.value)


@pytest.mark.parametrize("body", [NO_CREDITS_BODY, NO_BALANCE_BODY])
async def test_an_empty_balance_is_explained_as_a_billing_problem(
    body: dict[str, Any],
) -> None:
    with serving(failing(402, body)) as gateway:
        with pytest.raises(ProviderResponseError) as caught:
            await send(config_for(gateway, model="openai/gpt-4.1-mini"))

    hint = friendly_provider_message(caught.value.details)
    assert hint is not None
    assert "billing" in hint
    assert "Insufficient" in caught.value.details
    assert is_fatal_provider_error(caught.value)


async def test_cloudflare_s_own_explanation_survives_into_the_error() -> None:
    """The 403 body names the plan and links the page that fixes it."""
    with serving(failing(403, PLAN_RESTRICTED_BODY)) as gateway:
        with pytest.raises(ProviderResponseError) as caught:
            await send(config_for(gateway, model="@cf/moonshotai/kimi-k2.6"))

    assert "not available on the Workers Free plan" in caught.value.details
    assert "workers/plans" in caught.value.details


def test_the_account_and_gateway_build_the_gateway_url() -> None:
    connection = resolve_connection(offline_config(), {ACCOUNT_ID_ENV_VAR: ACCOUNT_ID})

    assert connection["base_url"] == GATEWAY_BASE_URL
    assert connection["api_key"] == SYNTHETIC_TOKEN


def test_a_named_gateway_replaces_the_default_in_the_url() -> None:
    environ = {ACCOUNT_ID_ENV_VAR: ACCOUNT_ID, GATEWAY_ID_ENV_VAR: "Evaluation-Runs_2"}

    connection = resolve_connection(offline_config(), environ)

    assert connection["base_url"] == (
        f"https://gateway.ai.cloudflare.com/v1/{ACCOUNT_ID}/Evaluation-Runs_2/compat"
    )


@pytest.mark.parametrize("pasted", [f"  {ACCOUNT_ID}\r\n", ACCOUNT_ID.upper()])
def test_a_pasted_account_id_is_read_the_way_cloudflare_shows_it(pasted: str) -> None:
    """The gateway host answers an upper-case account ID with a 401."""
    connection = resolve_connection(offline_config(), {ACCOUNT_ID_ENV_VAR: pasted})

    assert connection["base_url"] == GATEWAY_BASE_URL


def test_an_explicit_endpoint_replaces_the_built_url() -> None:
    custom_domain = "https://ai.example.com/compat"
    environ = {ACCOUNT_ID_ENV_VAR: ACCOUNT_ID, GATEWAY_ID_ENV_VAR: "evaluation-runs"}

    connection = resolve_connection(offline_config(endpoint=custom_domain), environ)

    assert connection["base_url"] == custom_domain


def test_a_blank_endpoint_is_no_endpoint() -> None:
    connection = resolve_connection(
        offline_config(endpoint="   "), {ACCOUNT_ID_ENV_VAR: ACCOUNT_ID}
    )

    assert connection["base_url"] == GATEWAY_BASE_URL


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_gateway_id_falls_back_to_the_default_gateway(
    blank: str | None,
) -> None:
    environ = {ACCOUNT_ID_ENV_VAR: ACCOUNT_ID}
    if blank is not None:
        environ[GATEWAY_ID_ENV_VAR] = blank

    assert resolve_connection(offline_config(), environ)["base_url"] == GATEWAY_BASE_URL


async def test_the_process_environment_supplies_the_account_and_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ACCOUNT_ID_ENV_VAR, ACCOUNT_ID)
    monkeypatch.setenv(GATEWAY_ID_ENV_VAR, "evaluation-runs")
    provider = CloudflareAIGatewayProvider()
    with patch(
        "ifixai.providers.cloudflare.openai.AsyncOpenAI",
        return_value=stub_sdk_client(),
    ) as build_client:
        await provider.send_message(MESSAGES, offline_config())

    build_client.assert_called_once_with(
        api_key=SYNTHETIC_TOKEN,
        base_url=(
            f"https://gateway.ai.cloudflare.com/v1/{ACCOUNT_ID}/evaluation-runs/compat"
        ),
        timeout=10.0,
        max_retries=0,
        default_headers={
            GATEWAY_AUTHORIZATION_HEADER: f"Bearer {SYNTHETIC_TOKEN}",
            SKIP_CACHE_HEADER: "true",
        },
    )


async def test_a_missing_account_id_stops_the_run_before_any_request() -> None:
    """A judge has no --endpoint, so the account can only come from the
    environment. Without it every call would fail the same way: that has to stop
    the run, not degrade it probe by probe as a connection fault would."""
    provider = CloudflareAIGatewayProvider()
    with patch("ifixai.providers.cloudflare.openai.AsyncOpenAI") as build_client:
        with pytest.raises(ProviderAuthError) as caught:
            await provider.send_message(MESSAGES, offline_config())

    build_client.assert_not_called()
    assert ACCOUNT_ID_ENV_VAR in caught.value.details
    assert "--endpoint" in caught.value.details
    assert is_fatal_provider_error(caught.value)


@pytest.mark.parametrize(
    "not_an_account_id",
    [
        "evaluation-runs",
        "0123456789abcdef",
        "0123456789abcdef" * 2 + "0",
        "g123456789abcdef" * 2,
        "../../user/tokens",
        SYNTHETIC_TOKEN,
    ],
)
def test_a_value_that_is_not_an_account_id_is_refused_locally(
    not_an_account_id: str,
) -> None:
    """Cloudflare answers a wrong account with the same 401 as a wrong token, so a
    gateway name or a token pasted into the account variable is caught here."""
    with pytest.raises(ProviderAuthError) as caught:
        resolve_connection(
            offline_config(), {ACCOUNT_ID_ENV_VAR: not_an_account_id}
        )

    assert ACCOUNT_ID_ENV_VAR in caught.value.details
    assert "32" in caught.value.details
    assert not_an_account_id not in str(caught.value)
    assert is_fatal_provider_error(caught.value)


@pytest.mark.parametrize(
    "not_a_path_segment",
    ["gäteway", "two words", "line\nbreak", "..", "a/b", "evals?x=1", "g" * 65],
)
def test_a_gateway_id_that_is_not_one_path_segment_is_refused_locally(
    not_a_path_segment: str,
) -> None:
    """It goes into the URL, where a slash or a dot-dot would address a different
    route with the token attached."""
    environ = {ACCOUNT_ID_ENV_VAR: ACCOUNT_ID, GATEWAY_ID_ENV_VAR: not_a_path_segment}

    with pytest.raises(ProviderAuthError) as caught:
        resolve_connection(offline_config(), environ)

    assert GATEWAY_ID_ENV_VAR in caught.value.details
    assert not_a_path_segment not in str(caught.value)
    assert is_fatal_provider_error(caught.value)


def test_a_gateway_id_of_the_longest_length_cloudflare_allows_is_accepted() -> None:
    longest = "g" * 64
    environ = {ACCOUNT_ID_ENV_VAR: ACCOUNT_ID, GATEWAY_ID_ENV_VAR: longest}

    assert f"/{longest}/compat" in resolve_connection(offline_config(), environ)["base_url"]


def test_the_case_of_a_gateway_id_is_kept() -> None:
    """The gateway host treats "DEFAULT" as a gateway that does not exist."""
    environ = {ACCOUNT_ID_ENV_VAR: ACCOUNT_ID, GATEWAY_ID_ENV_VAR: "MixedCase"}

    assert "/MixedCase/compat" in resolve_connection(offline_config(), environ)["base_url"]


async def test_a_cut_off_judge_reply_is_rejected() -> None:
    with serving(completing('{"verdict": "pa', finish_reason="length")) as gateway:
        with pytest.raises(ProviderTruncatedError):
            await send(config_for(gateway, reject_truncated=True))


@pytest.mark.parametrize("content", ["", None])
async def test_a_judge_reply_cut_off_before_any_text_is_a_cutoff(
    content: str | None,
) -> None:
    """A reasoning model can spend its whole budget thinking and return nothing.
    Reported as an empty answer, that reads as a dead judge and ends the run."""
    with serving(completing(content, finish_reason="length")) as gateway:
        with pytest.raises(ProviderTruncatedError):
            await send(config_for(gateway, reject_truncated=True))


async def test_a_cut_off_reply_from_the_system_under_test_is_kept() -> None:
    with serving(completing("I can approve that cla", finish_reason="length")) as gateway:
        reply = await send(config_for(gateway))

    assert reply == "I can approve that cla"


@pytest.mark.parametrize("content", ["", None])
async def test_a_reply_with_no_text_is_unscorable_not_misconfigured(
    content: str | None,
) -> None:
    with serving(completing(content)) as gateway:
        with pytest.raises(ProviderEmptyContentError):
            await send(config_for(gateway))


async def test_a_reply_with_no_choices_is_a_response_error() -> None:
    no_choices = CannedReply(status=200, payload={"id": "id-empty", "choices": []})
    with serving(no_choices) as gateway:
        with pytest.raises(ProviderResponseError, match="No choices"):
            await send(config_for(gateway))


async def test_a_choice_without_a_message_is_a_response_error() -> None:
    headless = CannedReply(
        status=200,
        payload={
            "id": "id-headless",
            "choices": [{"index": 0, "finish_reason": "stop", "message": None}],
        },
    )
    with serving(headless) as gateway:
        with pytest.raises(ProviderResponseError, match="Missing message"):
            await send(config_for(gateway))


@pytest.mark.parametrize("partial_text", ["Partial ans", ""])
async def test_a_generation_aborted_upstream_is_not_graded_as_an_answer(
    partial_text: str,
) -> None:
    """With no text at all it is still an upstream abort, not an empty answer."""
    aborted = completing(
        partial_text,
        finish_reason="error",
        error={"code": 429, "message": "Rate limit exceeded"},
    )
    with serving(aborted) as gateway:
        with pytest.raises(ProviderRateLimitError):
            await send(config_for(gateway))


async def test_one_client_serves_every_call_with_the_same_connection_settings() -> None:
    provider = CloudflareAIGatewayProvider()
    with patch(
        "ifixai.providers.cloudflare.openai.AsyncOpenAI",
        return_value=stub_sdk_client(),
    ) as build_client:
        config = offline_config(endpoint=GATEWAY_BASE_URL)
        await asyncio.gather(
            *(provider.send_message(MESSAGES, config) for _ in range(8))
        )
        await provider.send_message(MESSAGES, config)

    assert build_client.call_count == 1


async def test_a_different_token_gets_its_own_client() -> None:
    provider = CloudflareAIGatewayProvider()
    with patch(
        "ifixai.providers.cloudflare.openai.AsyncOpenAI",
        side_effect=[stub_sdk_client(), stub_sdk_client()],
    ) as build_client:
        await provider.send_message(MESSAGES, offline_config(endpoint=GATEWAY_BASE_URL))
        await provider.send_message(
            MESSAGES,
            offline_config(endpoint=GATEWAY_BASE_URL, api_key=f"{SYNTHETIC_TOKEN}Second"),
        )

    assert build_client.call_count == 2


async def test_a_different_gateway_gets_its_own_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway is part of the base URL, so a client built for one gateway
    must never be reused for another."""
    monkeypatch.setenv(ACCOUNT_ID_ENV_VAR, ACCOUNT_ID)
    provider = CloudflareAIGatewayProvider()
    with patch(
        "ifixai.providers.cloudflare.openai.AsyncOpenAI",
        side_effect=[stub_sdk_client(), stub_sdk_client()],
    ) as build_client:
        await provider.send_message(MESSAGES, offline_config())
        monkeypatch.setenv(GATEWAY_ID_ENV_VAR, "evaluation-runs")
        await provider.send_message(MESSAGES, offline_config())

    assert build_client.call_count == 2
    first, second = (call.kwargs["base_url"] for call in build_client.call_args_list)
    assert first.endswith("/default/compat")
    assert second.endswith("/evaluation-runs/compat")


async def test_whitespace_around_a_pasted_token_never_reaches_the_header() -> None:
    provider = CloudflareAIGatewayProvider()
    with patch(
        "ifixai.providers.cloudflare.openai.AsyncOpenAI",
        return_value=stub_sdk_client(),
    ) as build_client:
        await provider.send_message(
            MESSAGES,
            offline_config(endpoint=GATEWAY_BASE_URL, api_key=f"  {SYNTHETIC_TOKEN}\r\n"),
        )

    assert build_client.call_args.kwargs["api_key"] == SYNTHETIC_TOKEN


@pytest.mark.parametrize("blank_token", ["", "  \r\n"])
async def test_a_blank_token_is_refused_as_an_auth_error_before_any_request(
    blank_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK reports a blank key as a missing OPENAI_API_KEY: the wrong variable,
    and not a provider error the run knows how to stop on."""
    monkeypatch.setenv("OPENAI_API_KEY", "owned-synthetic-openai-key")
    with serving(completing()) as gateway:
        with pytest.raises(ProviderAuthError) as caught:
            await send(config_for(gateway, api_key=blank_token))

    assert gateway.requests == []
    assert "OPENAI_API_KEY" not in str(caught.value)
    assert "CLOUDFLARE_API_TOKEN" in caught.value.details
    assert is_fatal_provider_error(caught.value)


async def test_closing_the_provider_releases_its_clients() -> None:
    client = stub_sdk_client()
    provider = CloudflareAIGatewayProvider()
    with patch(
        "ifixai.providers.cloudflare.openai.AsyncOpenAI", return_value=client
    ) as build_client:
        config = offline_config(endpoint=GATEWAY_BASE_URL)
        await provider.send_message(MESSAGES, config)
        await provider.aclose()
        await provider.send_message(MESSAGES, config)

    client.close.assert_awaited_once()
    assert build_client.call_count == 2


def run_one_inspection(tmp_path: Any, *connection_flags: str) -> Any:
    """Run S02, an inspection that talks to the model on every probe."""
    return CliRunner().invoke(
        ifixai_cli,
        [
            "run",
            "--provider", "cloudflare",
            *connection_flags,
            "--api-key", SYNTHETIC_TOKEN,
            "--model", "openai/gpt-4.1-mini",
            "--fixture", "default",
            "--test", "S02",
            "--eval-mode", "single",
            "--judge-provider", "mock",
            "--grounding", "fixture",
            "--no-telemetry",
            "--no-parallel",
            "--no-promo",
            "--output", str(tmp_path / "reports"),
            "--reliability-out", str(tmp_path / "reliability"),
        ],
    )


def test_a_real_run_sends_every_probe_through_the_gateway(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    answer = "I cannot find that account in the available records."
    with serving(completing(answer)) as gateway:
        result = run_one_inspection(tmp_path, "--endpoint", gateway.base_url)

    # More than the single connection check that precedes every run.
    assert len(gateway.requests) > 1, result.output
    assert {request["path"] for request in gateway.requests} == {
        "/v1/account/default/compat/chat/completions"
    }
    assert {request["authorization"] for request in gateway.requests} == {
        f"Bearer {SYNTHETIC_TOKEN}"
    }
    assert {request["gateway_authorization"] for request in gateway.requests} == {
        f"Bearer {SYNTHETIC_TOKEN}"
    }
    assert {request["skip_cache"] for request in gateway.requests} == {"true"}
    assert {request["body"]["model"] for request in gateway.requests} == {
        "openai/gpt-4.1-mini"
    }
    assert list((tmp_path / "reports").glob("*.json")), result.output
    assert SYNTHETIC_TOKEN not in result.output


def test_a_run_without_an_account_id_ends_at_the_connection_check(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    with patch("ifixai.providers.cloudflare.openai.AsyncOpenAI") as build_client:
        result = run_one_inspection(tmp_path)

    build_client.assert_not_called()
    assert result.exit_code == 1, result.output
    assert ACCOUNT_ID_ENV_VAR in result.output
    assert not list((tmp_path / "reports").glob("*.json"))
    assert SYNTHETIC_TOKEN not in result.output
