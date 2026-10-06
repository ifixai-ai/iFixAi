import json

import pytest
from aiohttp import web

from ifixai.core.types import ExpectedClaim
from ifixai.evaluation.atomic_claims import score_atomic_claims_with_ground_truth
from ifixai.judge.config import JudgeConfig, JudgeProviderSpec
from ifixai.judge.evaluator import EnsembleJudgeEvaluator
from ifixai.providers import http


@pytest.mark.parametrize(
    "first_kind",
    ["missing", "extra", "malformed", "transport", "first-valid", "both-invalid"],
)
async def test_ensemble_uses_second_valid_judge_after_invalid_first(
    first_kind, monkeypatch
):
    expected = [
        ExpectedClaim(
            claim="Records expire after seven years",
            supported=True,
            evidence="policy",
            source_id="policy",
        ),
        ExpectedClaim(
            claim="Immediate deletion is allowed",
            supported=False,
            reason="No permission",
        ),
    ]
    correct = [
        {"claim": expected[0].claim, "response_correct": True, "reason": "asserted"},
        {
            "claim": expected[1].claim,
            "response_correct": True,
            "reason": "not asserted",
        },
    ]
    first = {
        "missing": json.dumps(correct[:1]),
        "extra": json.dumps([*correct, correct[0]]),
        "malformed": "not a verdict",
        "transport": "",
        "first-valid": json.dumps(
            [{**claim, "response_correct": False} for claim in correct]
        ),
        "both-invalid": json.dumps(correct[:1]),
    }[first_kind]
    requested_models = []

    async def complete(request):
        payload = await request.json()
        requested_models.append(payload["model"])
        assert payload["max_tokens"] == 2048
        assert expected[0].claim in payload["messages"][0]["content"]
        if payload["model"] == "first" and first_kind == "transport":
            return web.Response(status=400, text="judge unavailable")
        content = (
            first
            if payload["model"] == "first" or first_kind == "both-invalid"
            else json.dumps(correct)
        )
        return web.json_response(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ]
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    monkeypatch.setattr(
        http, "DEFAULT_ENDPOINT", f"http://127.0.0.1:{runner.addresses[0][1]}/v1"
    )
    judge = EnsembleJudgeEvaluator(
        JudgeConfig(
            endpoint=f"http://127.0.0.1:{runner.addresses[0][1]}/v1",
            providers=[
                JudgeProviderSpec(provider="http", model="first"),
                JudgeProviderSpec(provider="http", model="second"),
            ],
        )
    )
    try:
        score = await score_atomic_claims_with_ground_truth(
            "Records expire after seven years", expected, judge
        )
        if first_kind == "both-invalid":
            assert score.error == "all ensemble judges failed for ground-truth claims"
            assert score.score == 0.0
            assert not score.claims
        else:
            assert not score.error
            assert score.score == (0.0 if first_kind == "first-valid" else 1.0)
            assert score.total == 2
            assert score.supported == (0 if first_kind == "first-valid" else 2)
            assert [claim.claim for claim in score.claims] == [
                claim.claim for claim in expected
            ]
        assert sorted(requested_models) == ["first", "second"]
    finally:
        await judge.aclose()
        await runner.cleanup()


@pytest.mark.parametrize('custom_endpoint', [True, False])
async def test_public_ensemble_uses_configured_endpoint(custom_endpoint, monkeypatch, tmp_path):
    from ifixai.api import run_selected
    from ifixai.core.types import EvaluationPipelineConfig
    from ifixai.evaluation.analytic_judge import load_analytic_rubric
    from ifixai.reporting.scorecard import generate_json_report

    rubric = await load_analytic_rubric('B19', 'comply')
    calls = {'configured': [], 'default': []}

    def handler(route):
        async def complete(request):
            payload = await request.json()
            calls[route].append(payload['model'])
            verdict = route == 'configured' or not custom_endpoint
            content = json.dumps({'dimensions': [{'name': dim.name, 'passed': verdict, 'reasoning': 'owned control'} for dim in rubric.dimensions]}) if payload['model'] != 'sut' else 'The answer follows the supplied context.'
            return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})
        return complete

    runners = []
    endpoints = {}
    try:
        for route in ['configured', 'default']:
            app = web.Application()
            app.router.add_post('/chat/completions', handler(route))
            runner = web.AppRunner(app)
            runners.append(runner)
            await runner.setup()
            await web.TCPSite(runner, '127.0.0.1', 0).start()
            endpoints[route] = f'http://127.0.0.1:{runner.addresses[0][1]}'
        monkeypatch.setattr(http, 'DEFAULT_ENDPOINT', endpoints['default'])
        run = await run_selected(
            {'B19'}, provider='http', fixture='software_engineering', model='sut', endpoint=endpoints['configured'],
            judge_config=JudgeConfig(
                endpoint=endpoints['configured'] if custom_endpoint else None,
                providers=[JudgeProviderSpec(provider='http', model='first'), JudgeProviderSpec(provider='http', model='second')],
            ),
            pipeline_config=EvaluationPipelineConfig(judge_max_calls=0),
        )
        row = json.loads(generate_json_report(run))['test_results'][0]
        (tmp_path / 'scorecard.json').write_text(json.dumps({'custom_endpoint': custom_endpoint, 'calls': calls, 'row': row}, indent=2))
        assert row['status'] == 'pass'
        assert row['score'] == 1.0
        judge_route = 'configured' if custom_endpoint else 'default'
        assert calls[judge_route].count('first') == calls[judge_route].count('second') == 30
        other = 'default' if custom_endpoint else 'configured'
        assert calls[other].count('first') == calls[other].count('second') == 0
        assert calls['configured'].count('sut') == 60
    finally:
        for runner in runners:
            await runner.cleanup()
