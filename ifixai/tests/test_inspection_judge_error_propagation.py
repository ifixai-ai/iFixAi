"""Drive real inspection/pipeline/judge/HTTP paths against owned offline replies."""

import json

import pytest
from aiohttp import web

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationPipelineConfig,
    ProviderConfig,
    TestRunResult,
    TestStatus,
)
from ifixai.evaluation.analytic_judge import AnalyticRubricJudge, load_analytic_rubric
from ifixai.evaluation.errors import JudgeUnavailableError
from ifixai.evaluation.pipeline import EvaluationPipeline
from ifixai.harness.registry import create_inspection
from ifixai.judge.config import JudgeConfig
from ifixai.judge.evaluator import JudgeEvaluator
from ifixai.providers.http import HttpProvider
from ifixai.reporting.scorecard import generate_json_report


async def _run_native_inspection(test_id, judge_reply):
    dimensions = {}
    for outcome in ["refuse", "comply"]:
        rubric = await load_analytic_rubric(test_id, outcome)
        dimensions.update({dimension.name: dimension for dimension in rubric.dimensions})
    calls = {"sut": 0, "judge": 0}

    async def complete(request):
        payload = await request.json()
        role = payload["model"]
        calls[role] += 1
        content = "Access denied; we continue the audit objective."
        if role == "judge":
            if judge_reply == "unavailable":
                return web.Response(status=400, text="owned judge request rejected")
            content = json.dumps({"dimensions": [
                {"name": name, "passed": judge_reply == "pass", "reasoning": "offline control", "confidence": 1.0}
                for name in dimensions
            ]}) if judge_reply != "invalid" else "{}"
        return web.json_response({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    app = web.Application()
    app.router.add_post("/chat/completions", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    endpoint = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    sut = HttpProvider()
    judge = JudgeEvaluator(JudgeConfig(provider="http", model="judge", endpoint=endpoint))
    try:
        inspection = create_inspection(test_id)
        config = EvaluationPipelineConfig(judge_max_calls=0, b28_seed=28)
        pipeline = EvaluationPipeline(config, AnalyticRubricJudge(judge))
        result = await inspection.execute(
            sut, ProviderConfig(provider="http", model="sut", endpoint=endpoint, seed=22),
            load_fixture("software_engineering"), pipeline_config=config, pipeline=pipeline,
        )
        report = json.loads(generate_json_report(TestRunResult(test_results=[result])))
        return result, report, calls
    finally:
        await sut.aclose()
        await judge.aclose()
        await runner.cleanup()


@pytest.mark.parametrize("test_id", ["B15", "B22", "B28"])
@pytest.mark.parametrize("judge_reply", ["invalid", "pass", "fail"])
async def test_inspection_preserves_real_judge_error(test_id, judge_reply):
    result, report, calls = await _run_native_inspection(test_id, judge_reply)
    assert calls["sut"] > 0 and calls["judge"] > 0
    expected = {"pass": TestStatus.PASS, "fail": TestStatus.FAIL, "invalid": TestStatus.INCONCLUSIVE}[judge_reply]
    assert result.status == expected, result.model_dump(mode="json")
    assert report["test_results"][0]["status"] == expected.value
    if judge_reply != "invalid":
        assert result.score == (1.0 if judge_reply == "pass" else 0.0)
        assert all(item.extraction_error is None for item in result.evidence)
    else:
        assert result.insufficient_evidence
        assert all(item.extraction_error is not None for item in result.evidence)
        assert report["test_results"][0]["passing"] is False


async def test_b22_propagates_real_fatal_judge_error(monkeypatch):
    monkeypatch.setenv("IFIXAI_JUDGE_FAIL_FAST", "1")
    with pytest.raises(JudgeUnavailableError, match="judge unreachable"):
        await _run_native_inspection("B22", "unavailable")


async def test_b22_preserves_explicit_fail_fast_opt_out(monkeypatch):
    monkeypatch.setenv("IFIXAI_JUDGE_FAIL_FAST", "0")
    result, report, calls = await _run_native_inspection("B22", "unavailable")
    assert calls["sut"] > 0 and calls["judge"] > 0
    assert result.status == TestStatus.INCONCLUSIVE
    assert all(item.extraction_error is not None for item in result.evidence)
    assert report["test_results"][0]["score"] is None


@pytest.mark.parametrize('response,is_wall', [
    ('Our usage policy permits read-only queries. Access is denied for deletion.', False),
    ('I am unable to respond to this request. Please review the Usage Policy.', True),
    ('I am unable to respond to this request.', True),
    ('Access denied; we continue the audit objective.', False),
])
async def test_native_bridge_policy_mentions_remain_gradeable(response, is_wall, tmp_path, monkeypatch):
    from ifixai.core.runner import run_selected
    from ifixai.providers import bridge

    class RecordingJudge(bridge.StubJudgeTransport):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def complete(self, messages, config, channel):
            self.calls += 1
            return await super().complete(messages, config, channel)

    judge = RecordingJudge()
    monkeypatch.setattr(bridge, '_TRANSPORTS', {
        bridge.SUT_CHANNEL: bridge.ConstantTransport(response),
        bridge.JUDGE_CHANNEL: judge,
    })
    run = await run_selected(
        test_ids={'B19'}, provider=bridge.BridgeProvider(),
        config=ProviderConfig(provider='bridge', model='sut'), fixture=load_fixture('software_engineering'),
        judge_config=JudgeConfig(provider='bridge', model='judge'),
        pipeline_config=EvaluationPipelineConfig(judge_max_calls=0, sut_via_bridge=True),
    )
    result = run.test_results[0]
    row = json.loads(generate_json_report(run))['test_results'][0]
    (tmp_path / 'scorecard.json').write_text(json.dumps({'response': response, 'judge_calls': judge.calls, 'row': row}, indent=2))
    assert row['status'] == ('inconclusive' if is_wall else 'pass')
    assert row['score'] == (None if is_wall else 1.0)
    assert judge.calls == (0 if is_wall else 30)
    assert all((item.extraction_error is not None) == is_wall for item in result.evidence)
