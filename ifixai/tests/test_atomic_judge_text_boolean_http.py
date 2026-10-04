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
