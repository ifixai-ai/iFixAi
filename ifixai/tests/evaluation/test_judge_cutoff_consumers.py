from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.types import ExpectedClaim, ProviderConfig
from ifixai.evaluation.atomic_claims import (
    score_atomic_claims,
    score_atomic_claims_with_ground_truth,
)
from ifixai.evaluation.response_classifier import ResponseClass, classify_response
from ifixai.judge.config import JudgeConfig
from ifixai.judge.evaluator import JudgeEvaluator
from ifixai.providers.base import ProviderTruncatedError
from ifixai.providers.http import HttpProvider


@asynccontextmanager
async def local_judge(content, finish_reason):
    async def reply(request):
        await request.json()
        return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': finish_reason}]})
    app = web.Application()
    app.router.add_post('/v1/chat/completions', reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    endpoint = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1'
    try:
        yield endpoint
    finally:
        await runner.cleanup()


@pytest.mark.parametrize('finish_reason', ['length', 'stop'])
@pytest.mark.asyncio
async def test_atomic_consumer_rejects_native_http_cutoff(finish_reason):
    # A missing closing array/object is recoverable by json-repair, but a
    # transport-reported token cutoff is not a complete judge verdict.
    content = '[{"claim":"Refunds require a receipt","supported":true,"reason":"receipt matches"'
    if finish_reason == 'stop':
        content += '}]'
    async with local_judge(content, finish_reason) as endpoint:
        judge = JudgeEvaluator(JudgeConfig(provider='http', endpoint=endpoint))
        try:
            result = await score_atomic_claims('A receipt is required.', 'Receipt required.', 'grounding', judge)
            if finish_reason == 'length':
                assert result.error and result.total == 0
            else:
                assert not result.error and result.total == 1 and result.score == 1.0
        finally:
            await judge.aclose()


@pytest.mark.parametrize('finish_reason', ['length', 'stop'])
@pytest.mark.asyncio
async def test_ground_truth_consumer_rejects_native_http_cutoff(finish_reason):
    claim = ExpectedClaim(claim='Refunds require a receipt', supported=True, source_id='policy', evidence='Receipt required.')
    content = '[{"claim":"Refunds require a receipt","response_correct":true,"reason":"receipt matches"'
    if finish_reason == 'stop':
        content += '}]'
    async with local_judge(content, finish_reason) as endpoint:
        judge = JudgeEvaluator(JudgeConfig(provider='http', endpoint=endpoint))
        try:
            result = await score_atomic_claims_with_ground_truth('A receipt is required.', [claim], judge)
            if finish_reason == 'length':
                assert result.error and result.total == 0
            else:
                assert not result.error and result.total == 1 and result.score == 1.0
        finally:
            await judge.aclose()


@pytest.mark.parametrize('finish_reason', ['length', 'stop'])
@pytest.mark.asyncio
async def test_classifier_consumer_rejects_native_http_cutoff_but_sut_keeps_text(finish_reason):
    content = '{"class":"answer"}'
    async with local_judge(content, finish_reason) as endpoint:
        provider = HttpProvider()
        config = ProviderConfig(provider='http', endpoint=endpoint, max_retries=0)
        try:
            if finish_reason == 'length':
                with pytest.raises(ProviderTruncatedError):
                    await classify_response('A receipt is required.', 'Receipt policy?', provider, config)
            else:
                assert await classify_response('A receipt is required.', 'Receipt policy?', provider, config) is ResponseClass.ANSWER
            # Judge-only overrides must not mutate the SUT configuration.
            assert await provider.send_message([], config) == content
            assert config.reject_truncated is False
        finally:
            await provider.aclose()
