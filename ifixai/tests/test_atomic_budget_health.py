import json

from aiohttp import web

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationPipelineConfig,
    ExpectedClaim,
    JudgeErrorKind,
    ProviderConfig,
    TestRunResult,
)
from ifixai.evaluation.analytic_judge import AnalyticRubricJudge
from ifixai.evaluation.pipeline import EvaluationPipeline
from ifixai.harness.registry import create_inspection
from ifixai.judge.config import JudgeConfig
from ifixai.judge.evaluator import JudgeEvaluator
from ifixai.providers.http import HttpProvider
from ifixai.reporting.health import judge_health_note, run_health
from ifixai.reporting.scorecard import generate_json_report


async def test_b07_budget_skips_are_not_broken_judge_verdicts(tmp_path):
    calls = {'sut': 0, 'judge': 0}

    async def complete(request):
        payload = await request.json()
        role = payload['model']
        calls[role] += 1
        content = json.dumps([{'claim': 'Retention is seven years', 'response_correct': True, 'reason': 'policy'}]) if role == 'judge' else 'Retention is seven years.'
        return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})

    app = web.Application()
    app.router.add_post('/chat/completions', complete)
    server = web.AppRunner(app)
    await server.setup()
    await web.TCPSite(server, '127.0.0.1', 0).start()
    endpoint = f'http://127.0.0.1:{server.addresses[0][1]}'
    sut = HttpProvider()
    evaluator = JudgeEvaluator(JudgeConfig(provider='http', model='judge', endpoint=endpoint))
    fixture = load_fixture('software_engineering').model_copy(update={'expected_claims': [ExpectedClaim(claim='Retention is seven years', supported=True, evidence='policy', source_id='policy')]})
    config = EvaluationPipelineConfig(judge_max_calls=1)
    try:
        result = await create_inspection('B07').execute(sut, ProviderConfig(provider='http', model='sut', endpoint=endpoint), fixture, pipeline_config=config, pipeline=EvaluationPipeline(config, AnalyticRubricJudge(evaluator)))
        report = TestRunResult(test_results=[result])
        (tmp_path / 'scorecard.json').write_text(generate_json_report(report))
        health = run_health(report)
        assert calls['judge'] == 1
        assert len(result.evidence) > 1
        assert health.scorable == 1
        assert health.budget_skipped == len(result.evidence) - 1
        assert health.judge_broke == 0
        assert judge_health_note(health) is None
        assert sum(item.extraction_error == JudgeErrorKind.BUDGET for item in result.evidence) == len(result.evidence) - 1
    finally:
        await sut.aclose()
        await evaluator.aclose()
        await server.cleanup()
