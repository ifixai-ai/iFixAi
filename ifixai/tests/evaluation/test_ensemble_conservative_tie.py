"""Owned local HTTP judges exercise the real ensemble grading path."""
import json

import pytest
from aiohttp import web

from ifixai.core.types import AnalyticRubric, RubricDimension
from ifixai.evaluation.analytic_judge import EnsembleAnalyticRubricJudge
from ifixai.judge.config import JudgeConfig, JudgeProviderSpec
from ifixai.judge.evaluator import EnsembleJudgeEvaluator


@pytest.mark.parametrize('votes,mandatory,expected', [
    ([True, False], True, False),
    ([False, True], True, False),
    ([True, False], False, False),
    ([False, True], False, False),
    ([True, True], True, True),
    ([False, False], True, False),
    ([True, True, False], True, True),
    ([True, False, False], True, False),
])
async def test_native_http_ensemble_conservative_tie(votes, mandatory, expected):
    requests = []

    async def complete(request):
        payload = await request.json()
        requests.append(payload['model'])
        index = int(payload['model'].split('-')[-1])
        content = json.dumps({'dimensions': [{'name': 'accurate', 'passed': votes[index]}]})
        return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})

    app = web.Application()
    app.router.add_post('/v1/chat/completions', complete)
    server = web.AppRunner(app)
    await server.setup()
    await web.TCPSite(server, '127.0.0.1', 0).start()
    evaluator = EnsembleJudgeEvaluator(JudgeConfig(
        endpoint=f'http://127.0.0.1:{server.addresses[0][1]}/v1',
        providers=[JudgeProviderSpec(provider='http', model=f'judge-{i}') for i in range(len(votes))],
    ))
    rubric = AnalyticRubric(test_id='B06', outcome_type='comply', dimensions=[
        RubricDimension(name='accurate', description='Facts match.', weight=1.0, mandatory=mandatory),
    ])
    try:
        result = await EnsembleAnalyticRubricJudge(evaluator).evaluate_with_rubric('Receipt required.', rubric, 'policy')
        assert len(requests) == len(votes)
        assert len(result.per_judge) == len(votes)
        assert result.dimension_scores[0].passed is expected
        assert result.passed is expected
        assert result.verdict == ('pass' if expected else 'fail')
        assert result.mandatory_veto is (mandatory and not expected)
    finally:
        await evaluator.aclose()
        await server.cleanup()


@pytest.mark.parametrize('votes,mandatory,expected', [
    ([True, False], True, False),
    ([False, True], True, False),
    ([True, False], False, True),
    ([True, True, False], True, True),
    ([True, False, False], True, False),
])
async def test_native_http_tied_dimension_with_unanimous_other_dimension(votes, mandatory, expected):
    async def complete(request):
        payload = await request.json()
        index = int(payload['model'].split('-')[-1])
        content = json.dumps({'dimensions': [
            {'name': 'accurate', 'passed': votes[index]},
            {'name': 'complete', 'passed': True},
        ]})
        return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})

    app = web.Application()
    app.router.add_post('/v1/chat/completions', complete)
    server = web.AppRunner(app)
    await server.setup()
    await web.TCPSite(server, '127.0.0.1', 0).start()
    evaluator = EnsembleJudgeEvaluator(JudgeConfig(
        endpoint=f'http://127.0.0.1:{server.addresses[0][1]}/v1',
        providers=[JudgeProviderSpec(provider='http', model=f'judge-{i}') for i in range(len(votes))],
    ))
    rubric = AnalyticRubric(test_id='B06', outcome_type='comply', dimensions=[
        RubricDimension(name='accurate', description='Facts match.', weight=0.2, mandatory=mandatory),
        RubricDimension(name='complete', description='Covers required facts.', weight=0.8),
    ])
    try:
        result = await EnsembleAnalyticRubricJudge(evaluator).evaluate_with_rubric('Receipt required.', rubric, 'policy')
        mean_score = sum(v.weighted_score for v in result.per_judge) / len(votes)
        assert result.dimension_scores[0].passed is (sum(votes) > len(votes) / 2)
        assert result.dimension_scores[1].passed
        assert result.passed is expected
        assert result.mandatory_veto is (mandatory and not expected)
        if result.mandatory_veto:
            assert result.weighted_score == 0.0
            assert result.weighted_score_pre_veto == pytest.approx(mean_score)
        else:
            assert result.weighted_score == pytest.approx(mean_score)
        if not mandatory:
            # A tied optional dimension must not zero the existing 0.90 mean.
            assert mean_score == pytest.approx(0.9)
            assert result.verdict == 'pass'
        if mandatory and len(votes) == 2:
            # Native individual mandatory vetoes already zero failed judges;
            # therefore the ensemble mean cannot exceed 0.50 in this tie.
            assert mean_score == pytest.approx(0.5)
            assert result.per_judge[votes.index(False)].weighted_score_pre_veto == pytest.approx(0.8)
    finally:
        await evaluator.aclose()
        await server.cleanup()
