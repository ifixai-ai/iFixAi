"""Real optional SDK traffic to an owned loopback endpoint only."""

from contextlib import asynccontextmanager

import pytest
from aiohttp import web

pytest.importorskip("huggingface_hub")

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.evaluation.analytic_judge import (
    AnalyticRubricJudge,
    JudgeExtractionError,
    load_analytic_rubric,
)
from ifixai.judge.config import JudgeConfig
from ifixai.judge.evaluator import JudgeEvaluator
from ifixai.providers.base import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderOverloadedError,
    ProviderResponseError,
)
from ifixai.providers.huggingface import HuggingFaceProvider


@asynccontextmanager
async def endpoint(status, content="owned response"):
    calls = []

    async def complete(request):
        calls.append(await request.json())
        if status != 200:
            return web.Response(status=status, text="owned gateway response")
        return web.json_response({
            "id": "owned", "object": "chat.completion", "created": 0, "model": "owned-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
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
async def test_native_huggingface_gateway_failure_is_transient(status):
    expected = ProviderConnectionError if status == 503 else ProviderOverloadedError
    async with endpoint(status) as (url, calls):
        with pytest.raises(expected):
            await HuggingFaceProvider().send_message(
                [ChatMessage(content="owned prompt")],
                ProviderConfig(provider="huggingface", endpoint=url, model="owned-model", max_retries=0),
            )
        assert len(calls) == 1


@pytest.mark.parametrize("status,expected", [(400, ProviderResponseError), (401, ProviderAuthError), (403, ProviderAuthError)])
async def test_native_huggingface_request_errors_remain_hard(status, expected):
    async with endpoint(status) as (url, calls):
        with pytest.raises(expected):
            await HuggingFaceProvider().send_message(
                [ChatMessage(content="owned prompt")],
                ProviderConfig(provider="huggingface", endpoint=url, model="owned-model", max_retries=0),
            )
        assert len(calls) == 1


async def test_native_success_text_remains_unchanged():
    async with endpoint(200) as (url, calls):
        value = await HuggingFaceProvider().send_message(
            [ChatMessage(content="owned prompt")],
            ProviderConfig(provider="huggingface", endpoint=url, model="owned-model", max_retries=0),
        )
        assert value == "owned response"
        assert len(calls) == 1


async def test_native_judge_gateway_exhaustion_is_unscored(monkeypatch):
    import ifixai.evaluation.analytic_judge as analytic
    monkeypatch.setattr(analytic, "_BACKOFF_BASE", 0)
    async with endpoint(502) as (url, calls):
        evaluator = JudgeEvaluator(JudgeConfig(provider="huggingface", endpoint=url, model="owned-model"))
        try:
            with pytest.raises(JudgeExtractionError):
                await AnalyticRubricJudge(evaluator).evaluate_with_rubric(
                    "owned safe response", await load_analytic_rubric("B19", "comply"), "owned context",
                )
            assert len(calls) == 3
        finally:
            await evaluator.aclose()


async def run_native_inspection(monkeypatch=None):
    import json

    import ifixai.evaluation.analytic_judge as analytic
    from ifixai.core.fixture_loader import load_fixture
    from ifixai.core.types import EvaluationPipelineConfig, TestRunResult
    from ifixai.evaluation.pipeline import EvaluationPipeline
    from ifixai.harness.registry import create_inspection
    from ifixai.providers.base import ChatProvider
    from ifixai.reporting.scorecard import generate_json_report

    class OwnedSut(ChatProvider):
        async def send_message(self, messages, config):
            return "Owned offline SUT reply; no hosted model was contacted."

    old_backoff = analytic._BACKOFF_BASE
    analytic._BACKOFF_BASE = 0
    try:
        async with endpoint(502) as (url, calls):
            evaluator = JudgeEvaluator(JudgeConfig(provider="huggingface", endpoint=url, model="owned-model"))
            config = EvaluationPipelineConfig()
            pipeline = EvaluationPipeline(config, AnalyticRubricJudge(evaluator))
            try:
                result = await create_inspection("B19").execute(
                    OwnedSut(), ProviderConfig(provider="mock"), load_fixture("software_engineering"),
                    pipeline_config=config, pipeline=pipeline,
                )
                report = json.loads(generate_json_report(TestRunResult(test_results=[result])))
                return result, report, calls
            finally:
                await evaluator.aclose()
    finally:
        analytic._BACKOFF_BASE = old_backoff


async def test_inspection_scorecard_keeps_sdk_gateway_outage_unscored():
    from ifixai.core.types import TestStatus
    result, report, calls = await run_native_inspection()
    assert calls
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.insufficient_evidence
    assert report["test_results"][0]["status"] == "inconclusive"
    assert all(item.extraction_error is not None for item in result.evidence)
