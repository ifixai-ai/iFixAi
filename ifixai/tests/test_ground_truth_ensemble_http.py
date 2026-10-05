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
