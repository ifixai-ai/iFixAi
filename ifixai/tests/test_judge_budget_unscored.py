"""Budget-skipped native inspection evidence must retain unscored semantics."""

import json

import pytest
from aiohttp import web

from ifixai.api import run_selected
from ifixai.core.types import (
    EvaluationMode,
    EvaluationPipelineConfig,
    TestRunResult,
    TestStatus,
)
from ifixai.evaluation.analytic_judge import load_analytic_rubric
from ifixai.judge.config import JudgeConfig
from ifixai.reporting.health import (
    judge_health_note,
    measurement_failure_banner,
    run_health,
)
from ifixai.reporting.scorecard import extraction_error_warnings, generate_json_report


async def _run_with_budget(budget, passed=True, test_id="B19", communication_failure=False):
    dimensions = set()
    test_ids = {test_id} if isinstance(test_id, str) else test_id
    for selected in test_ids:
        for outcome in ["refuse", "comply"]:
            rubric = await load_analytic_rubric(selected, outcome)
            dimensions.update(dimension.name for dimension in rubric.dimensions)
    calls = {"sut": 0, "judge": 0}

    async def complete(request):
        payload = await request.json()
        role = payload["model"]
        calls[role] += 1
        content = "The answer follows the supplied context."
        if role == "judge":
            if communication_failure and calls["judge"] == 2:
                return web.Response(status=400, text="owned judge request rejected: quota exceeded")
            content = json.dumps({"dimensions": [
                {"name": name, "passed": passed, "reasoning": "offline control", "confidence": 1.0}
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
        run = await run_selected(
            test_ids, provider="http", fixture="software_engineering", model="sut", endpoint=endpoint,
            judge_config=JudgeConfig(provider="http", model="judge", endpoint=endpoint),
            pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL, judge_max_calls=budget),
        )
        result = run.test_results[0] if isinstance(test_id, str) else run
        return result, calls, json.loads(generate_json_report(run))
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("budget", [1, 24, 0])
@pytest.mark.parametrize("passed", [True, False])
async def test_public_b19_excludes_budget_skipped_evidence(budget, passed):
    result, calls, report = await _run_with_budget(budget, passed)
    assert calls["sut"] > 0
    assert calls["judge"] == (budget or len(result.evidence))
    skipped = [item for item in result.evidence if "budget exhausted" in item.evaluation_result]
    assert all(item.extraction_error is not None for item in skipped)
    measured = [item for item in result.evidence if item.extraction_error is None]
    assert len(measured) == calls["judge"]
    assert all(item.passed == passed for item in measured)
    health = run_health(TestRunResult(test_results=[result]))
    assert health.scorable == len(measured)
    if skipped:
        assert health.budget_skipped == len(skipped)
    assert health.judge_broke == health.unreachable == 0
    assert judge_health_note(health) is None
    warnings = extraction_error_warnings([result])
    assert not any("judge extraction failure" in warning for warning in warnings)
    assert bool(warnings) == bool(skipped)
    assert all("without a judge call" in warning for warning in warnings)
    if budget == 1:
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.insufficient_evidence
        assert report["test_results"][0]["score"] is None
    else:
        assert result.status == (TestStatus.PASS if passed else TestStatus.FAIL)
        assert result.score == (1.0 if passed else 0.0)
        assert not result.insufficient_evidence


@pytest.mark.parametrize("test_id", ["B17", "B29"])
async def test_declared_error_as_failure_policy_is_preserved(test_id):
    result, calls, report = await _run_with_budget(1, True, test_id)
    assert calls["judge"] == 1
    assert result.spec.count_extraction_errors_as_fail
    assert any("budget exhausted" in item.evaluation_result for item in result.evidence)
    assert result.status == TestStatus.FAIL
    assert not result.insufficient_evidence
    assert report["test_results"][0]["status"] == "fail"


async def test_all_skipped_native_inspection_gets_budget_health_banner():
    run, calls, report = await _run_with_budget(1, True, {"B19", "B20"})
    assert calls["judge"] == 1
    skipped_inspection = next(
        result for result in run.test_results
        if result.evidence and all("budget exhausted" in e.evaluation_result for e in result.evidence)
    )
    assert skipped_inspection.status == TestStatus.INCONCLUSIVE
    assert report["warnings"]
    assert not any("judge extraction failure" in warning for warning in report["warnings"])
    health = run_health(TestRunResult(test_results=[skipped_inspection]))
    assert health.invalid
    assert health.judge_broke == health.unreachable == health.scorable == 0
    assert health.budget_skipped == health.total
    banner = measurement_failure_banner(health)
    assert "judge budget was exhausted" in banner
    assert "no judge call was made" in banner
    assert "unreachable" not in banner and "broken grader" not in banner
    assert judge_health_note(health) is None


@pytest.mark.parametrize("budget", [2, 0])
async def test_health_transport_denominator_excludes_budget_skips(budget, monkeypatch):
    monkeypatch.setenv("IFIXAI_JUDGE_FAIL_FAST", "0")
    result, calls, _ = await _run_with_budget(budget, communication_failure=True)
    health = run_health(TestRunResult(test_results=[result]))
    assert health.unreachable == 1
    assert health.judge_broke == 0
    if budget:
        assert calls["judge"] == 2
        assert health.scorable == 1
        assert health.budget_skipped == 28
        assert health.attempted_probes == 2
        assert health.invalid
        banner = measurement_failure_banner(health)
        assert "1 of 2 attempted probes" in banner
        assert "1 of 30" not in banner
    else:
        assert calls["judge"] == health.total == 30
        assert health.scorable == 29
        assert not health.invalid
        assert measurement_failure_banner(health) is None
    assert judge_health_note(health) is None
