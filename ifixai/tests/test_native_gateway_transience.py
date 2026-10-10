"""Native SDK HTTP status classification against owned loopback traffic."""

from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.evaluation.analytic_judge import (
    AnalyticRubricJudge,
    JudgeExtractionError,
    load_analytic_rubric,
)
from ifixai.judge.config import JudgeConfig
from ifixai.judge.evaluator import JudgeEvaluator
from ifixai.providers.anthropic import AnthropicProvider
from ifixai.providers.base import (
    ProviderAuthError,
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderResponseError,
)


@asynccontextmanager
async def endpoint(status):
    calls = []

    async def complete(request):
        calls.append(await request.json())
        if status != 200:
            return web.json_response({"type": "error", "error": {"type": "owned_error", "message": "owned gateway response"}}, status=status)
        return web.json_response({
            "id": "owned", "type": "message", "role": "assistant", "model": "owned-model",
            "content": [{"type": "text", "text": "owned successful response"}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    app = web.Application()
    app.router.add_post('/{tail:.*}', complete)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield url, calls
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
async def test_anthropic_sdk_gateway_status_is_transient(status):
    async with endpoint(status) as (url, calls):
        provider = AnthropicProvider()
        try:
            with pytest.raises(ProviderOverloadedError):
                await provider.send_message([ChatMessage(content="owned prompt")], ProviderConfig(
                    provider="anthropic", endpoint=url, api_key="owned-fixture", model="owned-model", max_retries=0,
                ))
            assert len(calls) == 1
        finally:
            await provider.aclose()


@pytest.mark.parametrize("status,expected", [(400, ProviderResponseError), (401, ProviderAuthError), (403, ProviderResponseError), (429, ProviderRateLimitError)])
async def test_anthropic_sdk_hard_and_rate_statuses_are_preserved(status, expected):
    async with endpoint(status) as (url, calls):
        provider = AnthropicProvider()
        try:
            with pytest.raises(expected):
                await provider.send_message([ChatMessage(content="owned prompt")], ProviderConfig(
                    provider="anthropic", endpoint=url, api_key="owned-fixture", model="owned-model", max_retries=0,
                ))
            assert len(calls) == 1
        finally:
            await provider.aclose()


async def test_anthropic_sdk_success_is_preserved():
    async with endpoint(200) as (url, calls):
        provider = AnthropicProvider()
        try:
            result = await provider.send_message([ChatMessage(content="owned prompt")], ProviderConfig(
                provider="anthropic", endpoint=url, api_key="owned-fixture", model="owned-model", max_retries=0,
            ))
            assert result == "owned successful response"
            assert len(calls) == 1
        finally:
            await provider.aclose()


async def test_anthropic_sdk_gateway_exhaustion_is_unscored(monkeypatch):
    import ifixai.evaluation.analytic_judge as analytic
    monkeypatch.setattr(analytic, "_BACKOFF_BASE", 0)
    async with endpoint(502) as (url, calls):
        evaluator = JudgeEvaluator(JudgeConfig(provider="anthropic", endpoint=url, api_key="owned-fixture", model="owned-model"))
        try:
            with pytest.raises(JudgeExtractionError):
                await AnalyticRubricJudge(evaluator).evaluate_with_rubric(
                    "owned safe response", await load_analytic_rubric("B19", "comply"), "owned context",
                )
            assert len(calls) == 3
        finally:
            await evaluator.aclose()


@pytest.mark.parametrize("error_name,expected", [
    ("InternalServerError", ProviderOverloadedError),
    ("BadGateway", ProviderOverloadedError),
    ("GatewayTimeout", ProviderOverloadedError),
    ("ServiceUnavailable", None),
    ("BadRequest", ProviderResponseError),
    ("Unauthenticated", ProviderAuthError),
    ("PermissionDenied", ProviderAuthError),
    ("ResourceExhausted", ProviderRateLimitError),
])
async def test_gemini_native_sdk_gateway_exceptions_are_classified(monkeypatch, error_name, expected):
    glm = pytest.importorskip("google.ai.generativelanguage")
    exceptions = pytest.importorskip("google.api_core.exceptions")
    from ifixai.providers import gemini
    from ifixai.providers.base import ProviderConnectionError

    native_clients = []

    def request_client(**kwargs):
        native = glm.GenerativeServiceAsyncClient(**kwargs)
        native_clients.append(native)

        async def generate_content(request, **options):
            raise getattr(exceptions, error_name)("owned native SDK failure")

        monkeypatch.setattr(native, "generate_content", generate_content)
        return native

    monkeypatch.setattr(gemini, "GenerativeServiceAsyncClient", request_client)
    with pytest.raises(expected or ProviderConnectionError):
        await gemini.GeminiProvider().send_message([ChatMessage(content="owned prompt")], ProviderConfig(
            provider="gemini", api_key="owned-fixture", model="owned-model", max_retries=0,
        ))
    assert len(native_clients) == 1
    assert native_clients[0].transport.grpc_channel._channel.closed()


@asynccontextmanager
async def bedrock_endpoint(status):
    calls = []

    async def complete(request):
        calls.append(await request.json())
        if status != 200:
            code = {403: "AccessDeniedException", 429: "ThrottlingException", 503: "ServiceUnavailableException"}.get(status, "owned_error")
            return web.json_response({"__type": code, "message": "owned gateway response"}, status=status)
        return web.json_response({
            "output": {"message": {"role": "assistant", "content": [{"text": "owned successful response"}]}},
            "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 0},
        })

    app = web.Application()
    app.router.add_post('/{tail:.*}', complete)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield url, calls
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
async def test_bedrock_sdk_gateway_status_is_transient(monkeypatch, status):
    pytest.importorskip("boto3")
    from ifixai.providers.base import ProviderConnectionError
    from ifixai.providers.bedrock import BedrockProvider
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "owned-fixture")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "owned-fixture")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    async with bedrock_endpoint(status) as (url, calls):
        with pytest.raises(ProviderConnectionError):
            await BedrockProvider().send_message([ChatMessage(content="owned prompt")], ProviderConfig(
                provider="bedrock", endpoint=url, model="owned-model", max_retries=0,
            ))
        assert len(calls) == 1


@pytest.mark.parametrize("status,expected", [(400, ProviderResponseError), (403, ProviderAuthError), (429, ProviderRateLimitError), (200, None)])
async def test_bedrock_sdk_existing_response_paths_are_preserved(monkeypatch, status, expected):
    pytest.importorskip("boto3")
    from ifixai.providers.bedrock import BedrockProvider
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "owned-fixture")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "owned-fixture")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    async with bedrock_endpoint(status) as (url, calls):
        config = ProviderConfig(provider="bedrock", endpoint=url, model="owned-model", max_retries=0)
        if expected:
            with pytest.raises(expected):
                await BedrockProvider().send_message([ChatMessage(content="owned prompt")], config)
        else:
            assert await BedrockProvider().send_message([ChatMessage(content="owned prompt")], config) == "owned successful response"
        assert len(calls) == 1
