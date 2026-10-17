"""Native HTTP provider failures must retain other completed inspection probes.

The fixed grader isolates transport/evidence behavior; it makes no model-quality
claim and records whether failed replies ever reach scoring.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationMethod,
    EvaluationPipelineConfig,
    JudgeErrorKind,
    PipelineResult,
    ProviderConfig,
    TestStatus,
)
from ifixai.evaluation.atomic_claims import AtomicScore
from ifixai.evaluation.response_classifier import ResponseClass
from ifixai.inspections.b05_source_provenance.runner import B05SourceProvenance
from ifixai.inspections.b07_hallucination_rate.runner import B07HallucinationRate
from ifixai.inspections.b10_evaluation_drift.runner import B10EvaluationDrift
from ifixai.inspections.b12_prompt_injection.runner import B12PromptInjection
from ifixai.inspections.b15_long_horizon_drift.runner import B15LongHorizonDrift
from ifixai.inspections.b28_rag_context_integrity.runner import B28RAGContextIntegrity
from ifixai.inspections.b30_malicious_deployer_rules.runner import (
    B30MaliciousDeployerRules,
)
from ifixai.providers.http import HttpProvider
from ifixai.providers.langchain import LangChainProvider

RUNNERS = [B05SourceProvenance, B07HallucinationRate, B10EvaluationDrift,
           B12PromptInjection, B15LongHorizonDrift, B28RAGContextIntegrity, B30MaliciousDeployerRules]


class FixedGrader:
    def judge_budget_exhausted(self):
        return False

    def __init__(self):
        self.responses = []

    def judge_temperature(self):
        return 0

    def is_ensemble_judge(self):
        return True

    async def evaluate(self, response, **kwargs):
        self.responses.append(response)
        return PipelineResult(passed=True, evaluation_result="owned fixed grade", evaluation_method=EvaluationMethod.JUDGE)

    async def evaluate_atomic(self, response, **kwargs):
        self.responses.append(response)
        return AtomicScore(supported=1, total=1, score=1)

    async def classify(self, response, **kwargs):
        self.responses.append(response)
        return ResponseClass.REFUSAL


@asynccontextmanager
async def native_endpoint(kind, fail):
    calls = []

    async def complete(request):
        calls.append(await request.json())
        bad = fail == "all" or (fail == "first" and len(calls) == 1)
        if fail == "auth":
            return web.json_response({"error": "owned rejected credentials"}, status=401)
        if fail == "quota":
            return web.json_response({"error": "insufficient_quota"}, status=400)
        text = "" if bad else "owned completed response"
        if kind == "langchain":
            return web.json_response({"output": text})
        return web.json_response({"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]})

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


async def inspect(runner_type, kind, fail, *, endpoint_path=""):
    async with native_endpoint(kind, fail) as (url, calls):
        url += endpoint_path
        provider = LangChainProvider() if kind == "langchain" else HttpProvider()
        grader = FixedGrader()
        before_tasks = asyncio.all_tasks()
        try:
            result = await runner_type().execute(provider, ProviderConfig(provider=kind, endpoint=url, max_retries=0),
                load_fixture("customer_support"), pipeline_config=EvaluationPipelineConfig(b28_seed=42, b30_seed=42),
                pipeline=grader)
            return result, calls, grader.responses
        finally:
            # A fatal run exits early. Drain only this owned runner's queued
            # local requests before closing its provider and test server.
            pending = [task for task in asyncio.all_tasks() if task not in before_tasks
                       and task.get_coro().__qualname__.startswith(runner_type.__name__ + ".")]
            if pending:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 5)
            await provider.aclose()


@pytest.mark.parametrize("runner_type", RUNNERS, ids=lambda runner: runner.__name__)
@pytest.mark.parametrize("kind", ["http", "langchain"])
async def test_one_native_empty_reply_keeps_other_completed_probes(runner_type, kind):
    healthy, _, _ = await inspect(runner_type, kind, "none")
    result, calls, graded = await inspect(runner_type, kind, "first")
    assert len(healthy.evidence) > 1
    assert len(result.evidence) == len(healthy.evidence)
    errors = [item for item in result.evidence if item.extraction_error is not None]
    completed = [item for item in result.evidence if item.extraction_error is None]
    assert len(errors) == 1
    assert errors[0].extraction_error == JudgeErrorKind.COMMUNICATION
    assert not errors[0].passed
    assert completed
    assert all("owned completed response" in item.actual_response for item in completed)
    assert all(response and "error:" not in response for response in graded)
    assert len(calls) > 1
    assert result.score == runner_type().compute_score(completed)


@pytest.mark.parametrize("runner_type", RUNNERS, ids=lambda runner: runner.__name__)
@pytest.mark.parametrize("kind", ["http", "langchain"])
async def test_all_native_empty_replies_stay_visible_as_unscorable(runner_type, kind):
    result, calls, graded = await inspect(runner_type, kind, "all")
    assert result.evidence
    assert calls
    assert not graded
    assert all(item.extraction_error == JudgeErrorKind.COMMUNICATION and not item.passed for item in result.evidence)
    assert result.insufficient_evidence
    assert result.score == 0


@pytest.mark.parametrize("runner_type", RUNNERS, ids=lambda runner: runner.__name__)
@pytest.mark.parametrize("kind", ["http", "langchain"])
async def test_native_auth_failure_keeps_the_run_error_boundary(runner_type, kind):
    result, calls, graded = await inspect(runner_type, kind, "auth")
    assert result.status == TestStatus.ERROR
    assert not result.evidence
    assert not graded
    assert calls
    assert "401" in (result.error or "")


@pytest.mark.parametrize("delayed_status", [200, 401])
async def test_b10_awaits_sibling_framings_and_prioritizes_delayed_auth_failure(delayed_status):
    calls = []
    completed = []

    async def complete(request):
        await request.json()
        index = len(calls)
        calls.append(index)
        if index:
            await asyncio.sleep(0.1)
        completed.append(index)
        if index == 1 and delayed_status == 401:
            return web.json_response({"error": "owned rejected credentials"}, status=401)
        return web.json_response({"choices": [{"message": {"role": "assistant",
            "content": "" if index == 0 else "owned completed response"}, "finish_reason": "stop"}]})

    app = web.Application()
    app.router.add_post("/{tail:.*}", complete)
    server = web.AppRunner(app)
    await server.setup()
    site = web.TCPSite(server, "127.0.0.1", 0)
    await site.start()
    provider = HttpProvider()
    grader = FixedGrader()
    fixture = load_fixture("customer_support")
    fixture = fixture.model_copy(update={"users": fixture.users[:1],
                                         "high_risk_actions": [fixture.high_risk_actions[0]]})
    try:
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        result = await B10EvaluationDrift().execute(provider,
            ProviderConfig(provider="http", endpoint=url, max_retries=0), fixture,
            pipeline_config=EvaluationPipelineConfig(), pipeline=grader)
        assert len(calls) == 4
        assert sorted(completed) == [0, 1, 2, 3]
        assert not grader.responses
        if delayed_status == 401:
            assert result.status == TestStatus.ERROR
            assert "401" in (result.error or "")
            assert not result.evidence
        else:
            assert len(result.evidence) == 1
            assert result.evidence[0].extraction_error == JudgeErrorKind.COMMUNICATION
    finally:
        await provider.aclose()
        await server.cleanup()


@pytest.mark.parametrize("kind", ["http", "langchain"])
async def test_b12_one_fault_keeps_twenty_three_completed_payloads(kind):
    healthy, _, _ = await inspect(B12PromptInjection, kind, "none")
    result, calls, graded = await inspect(B12PromptInjection, kind, "first")
    assert len(healthy.evidence) == len(result.evidence) == 24
    assert len([item for item in result.evidence if item.extraction_error is None]) == 23
    assert len([item for item in result.evidence if item.extraction_error is not None]) == 1
    assert len(graded) == 23
    assert len(calls) == 24
    assert result.score == 1


@pytest.mark.parametrize("runner_type", [B10EvaluationDrift, B12PromptInjection, B30MaliciousDeployerRules])
@pytest.mark.parametrize("kind", ["http", "langchain"])
async def test_named_endpoint_keeps_completed_probes_and_unusable_reply_label(runner_type, kind):
    healthy, _, _ = await inspect(runner_type, kind, "none", endpoint_path="/billing-assistant")
    result, calls, graded = await inspect(runner_type, kind, "first", endpoint_path="/billing-assistant")
    assert result.status != TestStatus.ERROR
    assert len(result.evidence) == len(healthy.evidence)
    errors = [item for item in result.evidence if item.extraction_error is not None]
    completed = [item for item in result.evidence if item.extraction_error is None]
    assert len(errors) == 1
    assert errors[0].description.endswith("no usable reply from the agent")
    assert errors[0].details["comm_failure"] is True
    assert result.score == runner_type().compute_score(completed)
    assert len(graded) == len(completed)
    assert len(calls) > 1


@pytest.mark.parametrize("runner_type", [B10EvaluationDrift, B12PromptInjection, B30MaliciousDeployerRules])
async def test_native_http_spent_quota_still_stops_inspection(runner_type):
    result, calls, graded = await inspect(runner_type, "http", "quota")
    assert result.status == TestStatus.ERROR
    assert not result.evidence
    assert not graded
    assert calls
    assert "insufficient_quota" in (result.error or "")
