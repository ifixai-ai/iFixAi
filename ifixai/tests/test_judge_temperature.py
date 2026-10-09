"""Check public-run judge sampling through owned HTTP and inspection guards."""

import json

import pytest
from aiohttp import web

from ifixai.api import run_selected
from ifixai.core.types import EvaluationMode, EvaluationPipelineConfig, TestStatus
from ifixai.evaluation.analytic_judge import load_analytic_rubric
from ifixai.judge.config import JudgeConfig
from ifixai.reporting.scorecard import generate_json_report


async def _run_with_temperature(test_id, temperature=None):
    dimensions = set()
    for outcome in ["refuse", "comply"]:
        rubric = await load_analytic_rubric(test_id, outcome)
        dimensions.update(dimension.name for dimension in rubric.dimensions)
    payloads = []

    async def complete(request):
        payload = await request.json()
        payloads.append(payload)
        content = "Access denied; we continue the audit objective."
        if payload["model"] == "judge":
            content = json.dumps({"dimensions": [
                {"name": name, "passed": True, "reasoning": "offline control", "confidence": 1.0}
                for name in sorted(dimensions)
            ]})
        return web.json_response({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    app = web.Application()
    app.router.add_post("/chat/completions", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        endpoint = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        options = {} if temperature is None else {"temperature": temperature}
        run = await run_selected(
            {test_id}, provider="http", fixture="software_engineering", model="sut",
            endpoint=endpoint, sut_temperature=0.25, sut_seed=22,
            judge_config=JudgeConfig(provider="http", model="judge", endpoint=endpoint, **options),
            pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL, judge_max_calls=0),
        )
        report = json.loads(generate_json_report(run))
        return run.test_results[0], payloads, report
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("temperature", [None, 0.6])
async def test_public_run_sends_configured_judge_temperature(temperature):
    result, payloads, report = await _run_with_temperature("B29", temperature)
    assert result.status == TestStatus.PASS, result.model_dump(mode="json")
    judge_calls = [payload for payload in payloads if payload["model"] == "judge"]
    sut_calls = [payload for payload in payloads if payload["model"] == "sut"]
    assert judge_calls and sut_calls
    assert all(payload["temperature"] == (temperature or 0.0) for payload in judge_calls)
    assert all(payload["temperature"] == 0.25 for payload in sut_calls)
    assert report["test_results"][0]["status"] == "pass"


@pytest.mark.parametrize("temperature", [None, 0.6])
async def test_public_run_honors_existing_b15_determinism_guard(temperature):
    result, payloads, report = await _run_with_temperature("B15", temperature)
    if temperature is None:
        assert result.status == TestStatus.PASS, result.model_dump(mode="json")
        assert payloads
        assert all(payload["temperature"] == 0.0 for payload in payloads if payload["model"] == "judge")
    else:
        assert result.status == TestStatus.ERROR, result.model_dump(mode="json")
        assert "B15 requires judge temperature=0" in result.error_message
        assert "temperature=0.6" in result.error_message
        assert payloads == []
        assert report["test_results"][0]["status"] == "error"
