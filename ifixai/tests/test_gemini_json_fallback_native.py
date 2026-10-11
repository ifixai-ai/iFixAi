"""Actual Google SDK requests over an owned local gRPC transport."""

import json

import pytest
import pytest_asyncio

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTruncatedError,
)


def _config(**updates):
    return ProviderConfig(provider="gemini", model="gemini-owned", api_key="owned-key", timeout=2, max_retries=0, json_output=True, temperature=0.25, max_tokens=128, **updates)


@pytest_asyncio.fixture
async def native_endpoint(monkeypatch):
    grpc = pytest.importorskip("grpc")
    glm = pytest.importorskip("google.ai.generativelanguage")
    pytest.importorskip("google.generativeai")
    from google.ai.generativelanguage_v1beta.services.generative_service.transports.grpc_asyncio import (
        GenerativeServiceGrpcAsyncIOTransport,
    )
    from google.auth.credentials import AnonymousCredentials

    from ifixai.providers import gemini

    calls = []
    state = {"error": "JSON mode is not enabled for this model", "code": grpc.StatusCode.INVALID_ARGUMENT, "reject": True, "finish": glm.Candidate.FinishReason.STOP, "reply": "owned reply", "rate_limits": 0, "reject_plain": False}

    async def generate(request, context):
        calls.append(request)
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


@pytest.mark.parametrize("message", ["JSON mode is not enabled for this model", "Json mode is not enabled for tunedModels/owned", "response_mime_type application/json is not supported for this model"])
async def test_native_json_rejection_falls_back_once(native_endpoint, message):
    gemini, calls, state = native_endpoint
    state["error"] = message
    config = _config()
    assert await gemini.GeminiProvider().send_message([ChatMessage(role="system", content="Keep instructions"), ChatMessage(content="hello")], config) == "owned reply"
    assert [c.generation_config.response_mime_type for c in calls] == ["application/json", ""]
    assert calls[0].contents == calls[1].contents
    assert calls[0].system_instruction == calls[1].system_instruction
    assert calls[1].generation_config.temperature == pytest.approx(0.25)
    assert calls[1].generation_config.max_output_tokens == 128
    assert config.json_output is True


@pytest.mark.parametrize("json_output", [False, True])
async def test_supported_json_and_natural_sut_remain_single_calls(native_endpoint, json_output):
    gemini, calls, state = native_endpoint
    state["reject"] = False
    config = _config().model_copy(update={"json_output": json_output})
    assert await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], config) == "owned reply"
    assert [c.generation_config.response_mime_type for c in calls] == (["application/json"] if json_output else [""])


@pytest.mark.parametrize("message,auth", [("Invalid model name", False), ("JSON mode is enabled; max_output_tokens is not supported", False), ("response_mime_type application/json is valid; max_output_tokens is not supported", False), ("response_mime_type must have valid syntax", False), ("JSON mode is not enabled for this model", True)])
async def test_unrelated_invalid_argument_and_auth_do_not_fallback(native_endpoint, message, auth):
    gemini, calls, state = native_endpoint
    import grpc
    state.update(error=message, code=grpc.StatusCode.UNAUTHENTICATED if auth else grpc.StatusCode.INVALID_ARGUMENT)
    with pytest.raises(ProviderAuthError if auth else ProviderResponseError):
        await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], _config())
    assert len(calls) == 1


async def test_fallback_rejection_is_terminal(native_endpoint):
    gemini, calls, state = native_endpoint
    state["reject_plain"] = True
    with pytest.raises(ProviderResponseError):
        await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], _config())
    assert len(calls) == 2


async def test_fallback_preserves_cutoff_refusal(native_endpoint):
    gemini, calls, state = native_endpoint
    import google.ai.generativelanguage as glm
    state["finish"] = glm.Candidate.FinishReason.MAX_TOKENS
    with pytest.raises(ProviderTruncatedError):
        await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], _config(reject_truncated=True))
    assert len(calls) == 2


async def test_fallback_does_not_add_rate_limit_retry_budget(native_endpoint):
    gemini, calls, state = native_endpoint
    state["rate_limits"] = 1
    with pytest.raises(ProviderRateLimitError):
        await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], _config())
    assert len(calls) == 2


async def test_analytic_judge_consumes_free_text_fallback(native_endpoint):
    _gemini, calls, state = native_endpoint
    from ifixai.evaluation.analytic_judge import (
        AnalyticRubricJudge,
        load_analytic_rubric,
    )
    from ifixai.judge.config import JudgeConfig
    from ifixai.judge.evaluator import JudgeEvaluator
    rubric = await load_analytic_rubric("B19", "comply")
    state["reply"] = "```json\n" + json.dumps({"dimensions": [{"name": d.name, "passed": True, "reasoning": "owned offline control"} for d in rubric.dimensions]}) + "\n```"
    evaluator = JudgeEvaluator(JudgeConfig(provider="gemini", model="gemini-owned", api_key="owned-key", timeout=2))
    try:
        verdict = await AnalyticRubricJudge(evaluator).evaluate_with_rubric("Owned answer follows policy.", rubric, "owned context")
        assert verdict.passed
        assert verdict.weighted_score == 1.0
        assert [c.generation_config.response_mime_type for c in calls] == ["application/json", ""]
    finally:
        await evaluator.aclose()


async def test_public_fixture_run_exports_fallback_verdicts(native_endpoint):
    _gemini, calls, state = native_endpoint
    from ifixai.api import run_selected
    from ifixai.core.types import EvaluationMode, EvaluationPipelineConfig, TestStatus
    from ifixai.evaluation.analytic_judge import load_analytic_rubric
    from ifixai.judge.config import JudgeConfig
    from ifixai.reporting.scorecard import generate_json_report
    rubric = await load_analytic_rubric("B19", "comply")
    state["reply"] = "```json\n" + json.dumps({"dimensions": [{"name": d.name, "passed": True, "reasoning": "owned offline control"} for d in rubric.dimensions]}) + "\n```"
    result = await run_selected(
        {"B19"}, provider="mock", fixture="software_engineering", model="owned-sut",
        judge_config=JudgeConfig(provider="gemini", model="gemini-owned", api_key="owned-key", timeout=2),
        pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL, judge_max_calls=0),
    )
    report = json.loads(generate_json_report(result))
    assert result.test_results[0].status == TestStatus.PASS
    assert report["test_results"][0]["score"] == 1.0
    assert all(item.extraction_error is None for item in result.test_results[0].evidence)
    assert len(calls) == 60
    assert sum(c.generation_config.response_mime_type == "application/json" for c in calls) == 30
    assert sum(c.generation_config.response_mime_type == "" for c in calls) == 30
    print(json.dumps({"native_offline_scorecard": {"test_id": "B19", "status": report["test_results"][0]["status"], "score": report["test_results"][0]["score"], "native_judge_requests": len(calls)}}))
