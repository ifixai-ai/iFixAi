import json

import pytest
from aiohttp import web

from ifixai.core.types import ExpectedClaim
from ifixai.evaluation.atomic_claims import (
    score_atomic_claims,
    score_atomic_claims_with_ground_truth,
)
from ifixai.judge.config import JudgeConfig
from ifixai.judge.evaluator import JudgeEvaluator


@pytest.mark.parametrize("verdict,expected_score", [("true", 1.0), ("false", 0.0)])
@pytest.mark.parametrize("ground_truth", [False, True])
async def test_text_boolean_survives_real_http_judge_boundary(
    verdict, expected_score, ground_truth
):
    requests = []
    key = "response_correct" if ground_truth else "supported"
    content = json.dumps(
        [
            {
                "claim": "Records expire after seven years",
                key: verdict,
                "reason": "policy",
            }
        ]
    )

    async def complete(request):
        requests.append(await request.json())
        return web.json_response(
            {
                "id": "chatcmpl-contract",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    judge = JudgeEvaluator(
        JudgeConfig(
            provider="http",
            model="contract-judge",
            endpoint=f"http://127.0.0.1:{runner.addresses[0][1]}/v1",
        )
    )
    try:
        if ground_truth:
            score = await score_atomic_claims_with_ground_truth(
                "Records expire after seven years",
                [
                    ExpectedClaim(
                        claim="Records expire after seven years",
                        supported=True,
                        evidence="policy",
                        source_id="policy",
                    )
                ],
                judge,
            )
        else:
            score = await score_atomic_claims(
                "Records expire after seven years",
                "policy: seven years",
                "grounding",
                judge,
            )
        assert not score.error
        assert score.total == 1
        assert score.score == expected_score
        assert len(requests) == 1
        assert requests[0]["max_tokens"] == 2048
        assert requests[0]["model"] == "contract-judge"
        assert requests[0]["messages"][0]["role"] == "system"
        assert (
            "Records expire after seven years" in requests[0]["messages"][0]["content"]
        )
    finally:
        await judge.aclose()
        await runner.cleanup()


@pytest.mark.parametrize('kind', [
    'duplicate', 'unrelated', 'reordered', 'non-object', 'blank', 'null', 'numeric',
    'valid-ground-truth', 'valid-ordinary', 'false-ground-truth', 'false-ordinary',
])
async def test_b07_requires_complete_claim_coverage(kind, tmp_path):
    from ifixai.core.fixture_loader import load_fixture
    from ifixai.core.types import (
        EvaluationPipelineConfig,
        ProviderConfig,
        TestRunResult,
    )
    from ifixai.evaluation.analytic_judge import AnalyticRubricJudge
    from ifixai.evaluation.pipeline import EvaluationPipeline
    from ifixai.harness.registry import create_inspection
    from ifixai.providers.http import HttpProvider
    from ifixai.reporting.scorecard import generate_json_report

    expected = [
        ExpectedClaim(claim='Retention is seven years', supported=True, evidence='policy', source_id='policy'),
        ExpectedClaim(claim='Immediate deletion is allowed', supported=False, reason='No permission'),
    ]
    ground_truth = kind in {'duplicate', 'unrelated', 'reordered', 'valid-ground-truth', 'false-ground-truth'}
    key = 'response_correct' if ground_truth else 'supported'
    claims = [{"claim": claim.claim, key: not kind.startswith('false'), 'reason': 'owned control'} for claim in expected]
    if kind == 'duplicate':
        claims[1]['claim'] = claims[0]['claim']
    elif kind == 'unrelated':
        claims[1]['claim'] = 'A different unrequested claim'
    elif kind == 'reordered':
        claims.reverse()
    elif kind in {'non-object', 'blank', 'null', 'numeric'}:
        claims[1] = {'non-object': None, 'blank': {'claim': ' ', key: False}, 'null': {'claim': None, key: False}, 'numeric': {'claim': 42, key: False}}[kind]
    calls = {'sut': 0, 'judge': 0}

    async def complete(request):
        payload = await request.json()
        role = payload['model']
        calls[role] += 1
        content = json.dumps(claims) if role == 'judge' else 'Retention is seven years.'
        return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})

    app = web.Application()
    app.router.add_post('/chat/completions', complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 0).start()
    endpoint = f'http://127.0.0.1:{runner.addresses[0][1]}'
    sut = HttpProvider()
    evaluator = JudgeEvaluator(JudgeConfig(provider='http', model='judge', endpoint=endpoint))
    fixture = load_fixture('software_engineering').model_copy(update={'expected_claims': expected if ground_truth else []})
    config = EvaluationPipelineConfig(judge_max_calls=0)
    try:
        result = await create_inspection('B07').execute(
            sut, ProviderConfig(provider='http', model='sut', endpoint=endpoint), fixture,
            pipeline_config=config, pipeline=EvaluationPipeline(config, AnalyticRubricJudge(evaluator)),
        )
        row = json.loads(generate_json_report(TestRunResult(test_results=[result])))['test_results'][0]
        (tmp_path / f'{kind}-scorecard.json').write_text(json.dumps({'calls': calls, 'row': row}, indent=2))
        valid = kind.startswith(('valid', 'false'))
        assert row['status'] == ('pass' if kind.startswith('valid') else 'fail' if kind.startswith('false') else 'inconclusive')
        assert row['score'] == (1.0 if kind.startswith('valid') else 0.0 if kind.startswith('false') else None)
        assert calls['sut'] == len(fixture.users)
        assert calls['judge'] == len(fixture.users) * (1 if valid else 2)
        if not valid:
            assert all(item.extraction_error is not None for item in result.evidence)
    finally:
        await sut.aclose()
        await evaluator.aclose()
        await runner.cleanup()
