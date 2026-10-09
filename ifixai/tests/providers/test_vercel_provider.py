"""Vercel AI Gateway adapter: wire contract, error mapping and request shape.

The wire tests drive the real OpenAI SDK against a loopback stand-in for the
gateway and replay the error bodies Vercel documents. The 401 and 403 bodies
are the ones the live gateway returned, so the mapping is pinned to what the
SDK raises for real payloads rather than to hand-built exceptions.

References:
  https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions
  https://vercel.com/docs/ai-gateway/faq#why-did-my-ai-gateway-request-fail
  https://vercel.com/docs/ai-gateway/rate-limits
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
from ifixai.providers.vercel import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    MAX_TOKENS_CEILING,
    VercelAIGatewayProvider,
)

# Assembled at import time so the source holds no key-shaped literal for the
# repo's secret scanner (gitleaks, see .pre-commit-config.yaml) to flag.
SYNTHETIC_KEY = "vck_" + "syntheticLoopback" * 2
MESSAGES = [
    ChatMessage(role="system", content="You are a claims assistant."),
    ChatMessage(role="user", content="Reply with the single word: pong"),
]

AUTHENTICATION_FAILED_BODY = {
    "error": {
        "message": (
            "Authentication failed. Check that your Vercel credential is valid "
            "and has access to AI Gateway."
        ),
        "type": "authentication_error",
    }
}
CUSTOMER_VERIFICATION_REQUIRED_BODY = {
    "error": {
        "message": (
            "AI Gateway requires a valid credit card on file to service requests. "
            "Please visit https://vercel.com/d?to=%2F%5Bteam%5D%2F%7E%2Fai%3Fmodal"
            "%3Dadd-credit-card to add a card and unlock your free credits."
        ),
        "type": "customer_verification_required",
    }
}
BUDGET_EXCEEDED_BODY = {
    "error": {"message": "Budget reached.", "type": "quota_for_entity_exceeded"}
}
# Vercel documents the 402 status for an empty balance but not its body, so this
# wording deliberately matches none of the phrases the fail-fast path looks for.
EMPTY_BALANCE_BODY = {"error": {"message": "Top up to continue.", "type": "no_funds"}}
RATE_LIMITED_BODY = {
    "error": {"message": "Rate limit exceeded", "type": "rate_limit_exceeded"}
}
MISSING_PARAMETER_BODY = {
    "error": {
        "message": "Invalid request: missing required parameter 'model'",
        "type": "invalid_request_error",
        "param": "model",
        "code": "missing_parameter",
    }
}
MODEL_NOT_FOUND_BODY = {
    "error": {"message": "Model not found", "type": "model_not_found"}
}
UPSTREAM_FAILURE_BODY = {
    "error": {"message": "Upstream provider failed", "type": "internal_server_error"}
}
JSON_MODE_REJECTED_BODY = {
    "error": {
        "message": "response_format is not supported by this model",
        "type": "invalid_request_error",
    }
}


class RecordedRequest(TypedDict):
    path: str
    authorization: str | None
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
        return f"http://127.0.0.1:{self.server_port}/v1"

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
    choice = {
        "index": 0,
        "finish_reason": finish_reason,
        "message": {"role": "assistant", "content": content},
        **choice_extras,
    }
    return CannedReply(
        status=200,
        payload={
            "id": "gen_loopback",
            "object": "chat.completion",
            "created": 1,
            "model": DEFAULT_MODEL,
            "choices": [choice],
        },
    )


def offline_config(**overrides: Any) -> ProviderConfig:
    baseline = ProviderConfig(
        provider="vercel", api_key=SYNTHETIC_KEY, timeout=10, max_retries=0
    )
    return baseline.model_copy(update=overrides)


def config_for(gateway: GatewayStub, **overrides: Any) -> ProviderConfig:
    return offline_config(endpoint=gateway.base_url, **overrides)


async def send(config: ProviderConfig) -> str:
    provider = VercelAIGatewayProvider()
    try:
        return await provider.send_message(MESSAGES, config)
    finally:
        await provider.aclose()


async def test_a_call_reaches_the_chat_completions_route_with_the_bearer_key() -> None:
    with serving(completing("pong")) as gateway:
        reply = await send(config_for(gateway, model="anthropic/claude-haiku-4.5"))

    assert reply == "pong"
    [request] = gateway.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == f"Bearer {SYNTHETIC_KEY}"
    assert request["body"]["model"] == "anthropic/claude-haiku-4.5"
    assert request["body"]["messages"] == [
        {"role": "system", "content": "You are a claims assistant."},
        {"role": "user", "content": "Reply with the single word: pong"},
    ]


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
    assert "providerOptions" not in request["body"]


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
        (401, AUTHENTICATION_FAILED_BODY, ProviderAuthError, True),
        (402, BUDGET_EXCEEDED_BODY, ProviderResponseError, True),
        (402, EMPTY_BALANCE_BODY, ProviderResponseError, True),
        (403, CUSTOMER_VERIFICATION_REQUIRED_BODY, ProviderResponseError, True),
        (429, RATE_LIMITED_BODY, ProviderRateLimitError, False),
        (400, MISSING_PARAMETER_BODY, ProviderResponseError, False),
        (404, MODEL_NOT_FOUND_BODY, ProviderResponseError, False),
        (408, UPSTREAM_FAILURE_BODY, ProviderOverloadedError, False),
        (500, UPSTREAM_FAILURE_BODY, ProviderOverloadedError, False),
        (502, UPSTREAM_FAILURE_BODY, ProviderOverloadedError, False),
        (503, UPSTREAM_FAILURE_BODY, ProviderOverloadedError, False),
        (504, UPSTREAM_FAILURE_BODY, ProviderOverloadedError, False),
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
    assert caught.value.provider == "vercel"
    assert caught.value.endpoint == gateway.base_url
    assert str(status) in caught.value.details
    assert is_fatal_provider_error(caught.value) is aborts_the_run


async def test_an_empty_balance_is_explained_as_a_billing_problem() -> None:
    with serving(failing(402, EMPTY_BALANCE_BODY)) as gateway:
        with pytest.raises(ProviderResponseError) as caught:
            await send(config_for(gateway))

    hint = friendly_provider_message(caught.value.details)
    assert hint is not None
    assert "billing" in hint


async def test_the_gateway_s_own_explanation_survives_into_the_error() -> None:
    """The 403 body carries the link that fixes the account; it must not be lost."""
    with serving(failing(403, CUSTOMER_VERIFICATION_REQUIRED_BODY)) as gateway:
        with pytest.raises(ProviderResponseError) as caught:
            await send(config_for(gateway))

    assert "customer_verification_required" in caught.value.details
    assert "add-credit-card" in caught.value.details


async def test_a_cut_off_judge_reply_is_rejected() -> None:
    with serving(completing('{"verdict": "pa', finish_reason="length")) as gateway:
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
    no_choices = CannedReply(status=200, payload={"id": "gen_empty", "choices": []})
    with serving(no_choices) as gateway:
        with pytest.raises(ProviderResponseError, match="No choices"):
            await send(config_for(gateway))


async def test_a_choice_without_a_message_is_a_response_error() -> None:
    headless = CannedReply(
        status=200,
        payload={
            "id": "gen_headless",
            "choices": [{"index": 0, "finish_reason": "stop", "message": None}],
        },
    )
    with serving(headless) as gateway:
        with pytest.raises(ProviderResponseError, match="Missing message"):
            await send(config_for(gateway))


async def test_a_generation_aborted_upstream_is_not_graded_as_an_answer() -> None:
    aborted = completing(
        "Partial ans",
        finish_reason="error",
        error={"code": 429, "message": "Rate limit exceeded"},
    )
    with serving(aborted) as gateway:
        with pytest.raises(ProviderRateLimitError):
            await send(config_for(gateway))


def stub_sdk_client() -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(finish_reason="stop", error=None)]
    response.choices[0].message.content = "ok"
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    client.close = AsyncMock()
    return client


async def test_the_documented_gateway_url_is_the_default_endpoint() -> None:
    provider = VercelAIGatewayProvider()
    with patch(
        "ifixai.providers.vercel.openai.AsyncOpenAI", return_value=stub_sdk_client()
    ) as build_client:
        await provider.send_message(MESSAGES, offline_config())

    assert DEFAULT_BASE_URL == "https://ai-gateway.vercel.sh/v1"
    build_client.assert_called_once_with(
        api_key=SYNTHETIC_KEY, base_url=DEFAULT_BASE_URL, timeout=10.0, max_retries=0
    )


async def test_one_client_serves_every_call_with_the_same_connection_settings() -> None:
    provider = VercelAIGatewayProvider()
    with patch(
        "ifixai.providers.vercel.openai.AsyncOpenAI", return_value=stub_sdk_client()
    ) as build_client:
        await asyncio.gather(
            *(provider.send_message(MESSAGES, offline_config()) for _ in range(8))
        )
        await provider.send_message(MESSAGES, offline_config())

    assert build_client.call_count == 1


async def test_a_different_key_gets_its_own_client() -> None:
    provider = VercelAIGatewayProvider()
    with patch(
        "ifixai.providers.vercel.openai.AsyncOpenAI",
        side_effect=[stub_sdk_client(), stub_sdk_client()],
    ) as build_client:
        await provider.send_message(MESSAGES, offline_config())
        await provider.send_message(
            MESSAGES, offline_config(api_key=f"{SYNTHETIC_KEY}Second")
        )

    assert build_client.call_count == 2


async def test_whitespace_around_a_pasted_key_never_reaches_the_header() -> None:
    provider = VercelAIGatewayProvider()
    with patch(
        "ifixai.providers.vercel.openai.AsyncOpenAI", return_value=stub_sdk_client()
    ) as build_client:
        await provider.send_message(
            MESSAGES, offline_config(api_key=f"  {SYNTHETIC_KEY}\r\n")
        )

    assert build_client.call_args.kwargs["api_key"] == SYNTHETIC_KEY


@pytest.mark.parametrize("blank_key", ["", "  \r\n"])
async def test_a_blank_key_is_refused_as_an_auth_error_before_any_request(
    blank_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK reports a blank key as a missing OPENAI_API_KEY: the wrong variable,
    and not a provider error the run knows how to stop on."""
    monkeypatch.setenv("OPENAI_API_KEY", "owned-synthetic-openai-key")
    with serving(completing()) as gateway:
        with pytest.raises(ProviderAuthError) as caught:
            await send(config_for(gateway, api_key=blank_key))

    assert gateway.requests == []
    assert "OPENAI_API_KEY" not in str(caught.value)
    assert is_fatal_provider_error(caught.value)


async def test_closing_the_provider_releases_its_clients() -> None:
    client = stub_sdk_client()
    provider = VercelAIGatewayProvider()
    with patch(
        "ifixai.providers.vercel.openai.AsyncOpenAI", return_value=client
    ) as build_client:
        await provider.send_message(MESSAGES, offline_config())
        await provider.aclose()
        await provider.send_message(MESSAGES, offline_config())

    client.close.assert_awaited_once()
    assert build_client.call_count == 2


def test_a_real_run_sends_every_probe_through_the_gateway(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    answer = "I cannot find that account in the available records."
    with serving(completing(answer)) as gateway:
        result = CliRunner().invoke(
            ifixai_cli,
            [
                "run",
                "--provider", "vercel",
                "--endpoint", gateway.base_url,
                "--api-key", SYNTHETIC_KEY,
                "--model", "anthropic/claude-haiku-4.5",
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

    # More than the single connection check that precedes every run.
    assert len(gateway.requests) > 1, result.output
    assert {request["authorization"] for request in gateway.requests} == {
        f"Bearer {SYNTHETIC_KEY}"
    }
    assert {request["body"]["model"] for request in gateway.requests} == {
        "anthropic/claude-haiku-4.5"
    }
    assert list((tmp_path / "reports").glob("*.json")), result.output
    assert SYNTHETIC_KEY not in result.output
