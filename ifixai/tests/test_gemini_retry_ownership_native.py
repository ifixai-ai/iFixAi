"""Native Gemini SDK retries must not bypass adapter retry ownership."""

import pytest
import pytest_asyncio

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)


@pytest_asyncio.fixture
async def retry_endpoint(monkeypatch):
    grpc = pytest.importorskip("grpc")
    glm = pytest.importorskip("google.ai.generativelanguage")
    pytest.importorskip("google.generativeai")
    from google.ai.generativelanguage_v1beta.services.generative_service.transports.grpc_asyncio import (
        GenerativeServiceGrpcAsyncIOTransport,
    )
    from google.auth.credentials import AnonymousCredentials

    from ifixai.providers import gemini

    calls = []
    state = {"error": "JSON mode is not enabled for this model", "code": grpc.StatusCode.INVALID_ARGUMENT, "reject": False, "finish": glm.Candidate.FinishReason.STOP, "reply": "owned reply", "rate_limits": 0, "reject_plain": False}

    async def generate(request, context):
        calls.append(request)
        if state.get("delay"):
            import asyncio
            await asyncio.sleep(state["delay"])
        if state.get("unavailable", 0):
            if state["unavailable"] > 0:
                state["unavailable"] -= 1
            await context.abort(grpc.StatusCode.UNAVAILABLE, "owned unavailable")
        if state["reject"] and (request.generation_config.response_mime_type or state["reject_plain"]):
            await context.abort(state["code"], state["error"])
        if state["rate_limits"]:
            state["rate_limits"] -= 1
            await context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "owned throttle")
        return glm.GenerateContentResponse(candidates=[glm.Candidate(content=glm.Content(parts=[glm.Part(text=state["reply"])]), finish_reason=state["finish"])])

    server = grpc.aio.server()
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("google.ai.generativelanguage.v1beta.GenerativeService", {"GenerateContent": grpc.unary_unary_rpc_method_handler(generate, request_deserializer=glm.GenerateContentRequest.deserialize, response_serializer=glm.GenerateContentResponse.serialize)}),))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    clients = []

    def request_client(**kwargs):
        # Substitute only SDK transport routing: the actual generated client,
        # protobuf request serialization, RPC and Google exception mapping run.
        transport = GenerativeServiceGrpcAsyncIOTransport(channel=grpc.aio.insecure_channel(f"127.0.0.1:{port}"), credentials=AnonymousCredentials())
        client = glm.GenerativeServiceAsyncClient(transport=transport)
        clients.append(client)
        return client

    monkeypatch.setattr(gemini, "GenerativeServiceAsyncClient", request_client)
    try:
        yield gemini, calls, state
    finally:
        for client in clients:
            await client.transport.close()
        await server.stop(0)
        await server.wait_for_termination(timeout=2)



@pytest.mark.parametrize("unavailable", [1, -1])
@pytest.mark.parametrize("max_retries", [0, 2])
async def test_sdk_503_does_not_retry_behind_adapter(retry_endpoint, unavailable, max_retries):
    gemini, calls, state = retry_endpoint
    state["unavailable"] = unavailable
    config = ProviderConfig(provider="gemini", model="gemini-owned", api_key="owned-key", timeout=2, max_retries=max_retries)
    try:
        with pytest.raises(ProviderConnectionError):
            await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], config)
    finally:
        print({"native_requests": len(calls), "unavailable": unavailable, "max_retries": max_retries})
    assert len(calls) == 1


@pytest.mark.parametrize("max_retries", [0, 1])
async def test_adapter_keeps_explicit_rate_limit_budget(retry_endpoint, max_retries):
    gemini, calls, state = retry_endpoint
    state["rate_limits"] = 1
    config = ProviderConfig(provider="gemini", model="gemini-owned", api_key="owned-key", timeout=2, max_retries=max_retries)
    if max_retries:
        assert await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], config) == "owned reply"
    else:
        with pytest.raises(ProviderRateLimitError):
            await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], config)
    assert len(calls) == max_retries + 1


async def test_success_is_one_native_request(retry_endpoint):
    gemini, calls, _state = retry_endpoint
    config = ProviderConfig(provider="gemini", model="gemini-owned", api_key="owned-key", timeout=2, max_retries=2)
    assert await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], config) == "owned reply"
    assert len(calls) == 1


async def test_public_fixture_run_records_native_outage_as_unscored(retry_endpoint):
    import json

    from ifixai.api import run_selected
    from ifixai.core.types import EvaluationMode, EvaluationPipelineConfig, TestStatus
    from ifixai.judge.config import JudgeConfig
    from ifixai.reporting.scorecard import generate_json_report
    _gemini, calls, state = retry_endpoint
    state["unavailable"] = -1
    result = await run_selected(
        {"B19"}, provider="mock", fixture="software_engineering", model="owned-sut",
        judge_config=JudgeConfig(provider="gemini", model="gemini-owned", api_key="owned-key", timeout=2),
        pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL, judge_max_calls=0),
    )
    report = json.loads(generate_json_report(result))
    assert result.test_results[0].status == TestStatus.INCONCLUSIVE
    assert report["test_results"][0]["score"] is None
    assert calls
    print(json.dumps({"native_offline_scorecard": {"test_id": "B19", "status": report["test_results"][0]["status"], "score": report["test_results"][0]["score"], "native_judge_requests": len(calls)}}))


async def test_native_request_keeps_configured_timeout(retry_endpoint):
    gemini, calls, state = retry_endpoint
    state["delay"] = 2
    config = ProviderConfig(provider="gemini", model="gemini-owned", api_key="owned-key", timeout=1, max_retries=2)
    with pytest.raises(ProviderTimeoutError):
        await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], config)
    assert len(calls) == 1
