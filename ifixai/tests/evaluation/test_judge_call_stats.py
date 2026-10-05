import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.types import (
    AnalyticRubric,
    EvaluationCriteria,
    EvaluationPipelineConfig,
    RubricDimension,
    TestRunResult,
)
from ifixai.evaluation.analytic_judge import (
    AnalyticRubricJudge,
    EnsembleAnalyticRubricJudge,
)
from ifixai.evaluation.pipeline import EvaluationPipeline
from ifixai.judge.config import JudgeConfig, JudgeProviderSpec
from ifixai.judge.evaluator import EnsembleJudgeEvaluator, JudgeEvaluator
from ifixai.reporting.scorecard import generate_json_report

RUBRIC = AnalyticRubric(test_id='B06', outcome_type='comply', dimensions=[
    RubricDimension(name='accurate', description='Facts match.', weight=1.0),
])


@asynccontextmanager
async def native_http_judge():
    requests = []
    async def reply(request):
        body = await request.json()
        requests.append(body)
        prompt = body['messages'][0]['content']
        if len(requests) == 1:
            content = 'not JSON'
        elif prompt.startswith('Classify assistant responses'):
            content = '{"class":"answer"}'
        elif 'atomic claim' in prompt:
            content = '[{"claim":"Receipt required","supported":true,"reason":"policy"}]'
        else:
            content = '{"dimensions":[{"name":"accurate","passed":true}]}'
        return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})
    app = web.Application()
    app.router.add_post('/v1/chat/completions', reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    endpoint = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1'
    try:
        yield endpoint, requests
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_native_judge_stats_count_retries_atomic_and_classifier_calls():
    async with native_http_judge() as (endpoint, requests):
        evaluator = JudgeEvaluator(JudgeConfig(provider='http', endpoint=endpoint))
        pipeline = EvaluationPipeline(EvaluationPipelineConfig(), AnalyticRubricJudge(evaluator))
        try:
            assert (await pipeline.evaluate('Receipt required.', EvaluationCriteria(), RUBRIC)).passed
            assert evaluator.get_stats()['total_calls'] == len(requests) == 2
            atomic = await pipeline.evaluate_atomic('Receipt required.', 'Receipt required.', 'grounding')
            assert atomic is not None and not atomic.error
            assert evaluator.get_stats()['total_calls'] == len(requests) == 3
            assert await pipeline.classify('Receipt required.', 'Receipt policy?') is not None
            stats = evaluator.get_stats()
            assert stats['total_calls'] == stats['items_escalated'] == len(requests) == 4
            assert pipeline.judge_calls_used == 3, 'budget counts logical grades, stats count provider sends'
            scorecard = json.loads(generate_json_report(TestRunResult(judge_stats=stats)))
            assert scorecard['metadata']['judge_stats']['total_calls'] == 4
        finally:
            await evaluator.aclose()


@pytest.mark.asyncio
async def test_ensemble_stats_sum_each_judge_send():
    evaluator = EnsembleJudgeEvaluator(JudgeConfig(providers=[
        JudgeProviderSpec(provider='mock'), JudgeProviderSpec(provider='mock'),
    ]))
    judge = EnsembleAnalyticRubricJudge(evaluator)
    try:
        verdict = await judge.evaluate_with_rubric('Receipt required.', RUBRIC, 'receipt policy')
        assert len(verdict.per_judge) == 2
        stats = evaluator.get_stats()
        assert stats['total_calls'] == 2
        assert [s['total_calls'] for s in stats['per_judge_stats']] == [1, 1]
    finally:
        await evaluator.aclose()
