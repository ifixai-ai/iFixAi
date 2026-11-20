"""Native SDK and inspection controls against an owned transient HTTP server."""

import json
import time
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import ChatMessage, EvaluationPipelineConfig, ProviderConfig
from ifixai.evaluation.analytic_judge import AnalyticRubricJudge, load_analytic_rubric
from ifixai.evaluation.pipeline import EvaluationPipeline
from ifixai.inspections.b19_context_accuracy.runner import B19ContextAccuracy
from ifixai.judge.config import JudgeConfig
from ifixai.judge.evaluator import JudgeEvaluator
from ifixai.providers.base import ProviderOverloadedError, ProviderResponseError


@pytest.fixture
def native_provider():
    pytest.importorskip("anthropic")
    from ifixai.providers.anthropic import AnthropicProvider

    return AnthropicProvider


@asynccontextmanager
async def transient_endpoint(text, *, status=529, duration=0, failures=0):
    calls = []
    started = None

    async def complete(request):
        nonlocal started
        calls.append(await request.json())
        started = started if started is not None else time.monotonic()
        if len(calls) <= failures or time.monotonic() - started < duration:
            return web.json_response({"type": "error", "error": {
                "type": "owned_error", "message": "owned temporary outage",
            }}, status=status)
        return web.json_response({
            "id": "owned", "type": "message", "role": "assistant", "model": "owned-model",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    app = web.Application()
    app.router.add_post("/{tail:.*}", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", calls
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504, 529])
async def test_transient_native_status_recovers_with_configured_retry(monkeypatch, native_provider, status):
    from ifixai.providers import anthropic as module

    monkeypatch.setattr(module, "INITIAL_BACKOFF_SECONDS", 0.01)
    async with transient_endpoint("owned reply", status=status, failures=1) as (url, calls):
        provider = native_provider()
        try:
            assert await provider.send_message([ChatMessage(content="owned prompt")], ProviderConfig(
                provider="anthropic", endpoint=url, api_key="owned-fixture", model="owned-model", max_retries=1,
            )) == "owned reply"
            assert len(calls) == 2
        finally:
            await provider.aclose()


async def test_exhausted_native_overload_preserves_type_and_retry_budget(monkeypatch, native_provider):
    from ifixai.providers import anthropic as module

    monkeypatch.setattr(module, "INITIAL_BACKOFF_SECONDS", 0.01)
    async with transient_endpoint("owned reply", failures=100) as (url, calls):
        provider = native_provider()
        try:
            with pytest.raises(ProviderOverloadedError):
                await provider.send_message([ChatMessage(content="owned prompt")], ProviderConfig(
                    provider="anthropic", endpoint=url, api_key="owned-fixture", model="owned-model", max_retries=2,
                ))
            assert len(calls) == 3
        finally:
            await provider.aclose()


async def test_hard_native_status_is_not_retried(native_provider):
    async with transient_endpoint("owned reply", status=400, failures=100) as (url, calls):
        provider = native_provider()
        try:
            with pytest.raises(ProviderResponseError):
                await provider.send_message([ChatMessage(content="owned prompt")], ProviderConfig(
                    provider="anthropic", endpoint=url, api_key="owned-fixture", model="owned-model", max_retries=3,
                ))
            assert len(calls) == 1
        finally:
            await provider.aclose()


@asynccontextmanager
async def dropping_endpoint(text, *, drops=1):
    calls = []

    async def complete(request):
        calls.append(await request.json())
        if len(calls) <= drops:
            request.transport.close()
            return web.Response(status=500)
        return web.json_response({
            "id": "owned", "type": "message", "role": "assistant", "model": "owned-model",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    app = web.Application()
    app.router.add_post("/{tail:.*}", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", calls
    finally:
        await runner.cleanup()


async def test_dropped_connection_recovers_with_configured_retry(monkeypatch, native_provider):
    from ifixai.providers import anthropic as module

    monkeypatch.setattr(module, "INITIAL_BACKOFF_SECONDS", 0.01)
    async with dropping_endpoint("owned reply") as (url, calls):
        provider = native_provider()
        try:
            assert await provider.send_message([ChatMessage(content="owned prompt")], ProviderConfig(
                provider="anthropic", endpoint=url, api_key="owned-fixture", model="owned-model", max_retries=1,
            )) == "owned reply"
            assert len(calls) == 2
        finally:
            await provider.aclose()


@pytest.mark.parametrize("outage_target", ["agent", "judge"])
async def test_b19_keeps_all_thirty_grades_after_three_second_529_blip(native_provider, outage_target):
    rubric = await load_analytic_rubric("B19", "comply")
    verdict = json.dumps({"dimensions": [{"name": dimension.name, "passed": True,
                          "reasoning": "owned fixture"} for dimension in rubric.dimensions],
                          "overall_reasoning": "owned fixture"})
    async with transient_endpoint("owned context response", duration=3 if outage_target == "agent" else 0) as (agent_url, _):
        async with transient_endpoint(verdict, duration=3 if outage_target == "judge" else 0) as (judge_url, _):
            provider = native_provider()
            evaluator = JudgeEvaluator(JudgeConfig(provider="anthropic", endpoint=judge_url,
                api_key="owned-fixture", model="owned-model"))
            config = EvaluationPipelineConfig()
            try:
                result = await B19ContextAccuracy().execute(provider,
                    ProviderConfig(provider="anthropic", endpoint=agent_url, api_key="owned-fixture", model="owned-model"),
                    load_fixture("customer_support"), pipeline_config=config,
                    pipeline=EvaluationPipeline(config, judge=AnalyticRubricJudge(evaluator)))
                assert len(result.evidence) == 30
                assert all(item.rubric_verdict is not None for item in result.evidence)
                assert all(not item.extraction_error for item in result.evidence)
            finally:
                await provider.aclose()
                await evaluator.aclose()
