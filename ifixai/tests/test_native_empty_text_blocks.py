"""Empty native text blocks must not be graded as completed textual answers."""
from contextlib import asynccontextmanager

import aiohttp.web
import pytest

from ifixai.api import run_single
from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    ChatMessage,
    EvaluationPipelineConfig,
    ProviderConfig,
    TestStatus,
)
from ifixai.judge.config import JudgeConfig
from ifixai.providers.base import ProviderEmptyContentError, ProviderTruncatedError


@asynccontextmanager
async def native_endpoint(provider, texts, stop_reason=None):
    requests = []
    async def respond(request):
        requests.append(await request.json())
        if provider == "anthropic":
            return aiohttp.web.json_response({
                "id": "msg_owned", "type": "message", "role": "assistant", "model": "owned",
                "content": [{"type": "text", "text": text} for text in texts],
                "stop_reason": stop_reason or "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        return aiohttp.web.json_response({
            "output": {"message": {"role": "assistant", "content": [{"text": text} for text in texts]}},
            "stopReason": stop_reason or "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        })
    app = aiohttp.web.Application()
    app.router.add_post('/{path:.*}', respond)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}", requests
    finally:
        await runner.cleanup()


def native_provider(name, monkeypatch):
    if name == "anthropic":
        pytest.importorskip('anthropic')
        from ifixai.providers.anthropic import AnthropicProvider
        return AnthropicProvider()
    pytest.importorskip('boto3')
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'owned-fixture-key')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'owned-fixture-secret')
    monkeypatch.setenv('AWS_EC2_METADATA_DISABLED', 'true')
    from ifixai.providers.bedrock import BedrockProvider
    return BedrockProvider()


def native_config(name, endpoint, reject_truncated=False):
    return ProviderConfig(provider=name, endpoint=endpoint, model='owned', api_key='owned-fixture',
                          max_retries=0, timeout=3, reject_truncated=reject_truncated)


@pytest.mark.parametrize('name', ['anthropic', 'bedrock'])
@pytest.mark.parametrize('texts', [[''], ['', '']])
async def test_only_empty_text_blocks_raise_completed_empty(name, texts, monkeypatch):
    async with native_endpoint(name, texts) as (endpoint, requests):
        provider = native_provider(name, monkeypatch)
        try:
            with pytest.raises(ProviderEmptyContentError):
                await provider.send_message([ChatMessage(content='owned prompt')], native_config(name, endpoint))
        finally:
            await provider.aclose()
        assert len(requests) == 1


@pytest.mark.parametrize('name', ['anthropic', 'bedrock'])
@pytest.mark.parametrize('texts,expected', [(['', 'visible', ''], '\nvisible\n'), (['  \t'], '  \t'), (['one', 'two'], 'one\ntwo')])
async def test_visible_mixed_blocks_and_whitespace_keep_existing_output(name, texts, expected, monkeypatch):
    async with native_endpoint(name, texts) as (endpoint, requests):
        provider = native_provider(name, monkeypatch)
        try:
            assert await provider.send_message([ChatMessage(content='owned prompt')], native_config(name, endpoint)) == expected
        finally:
            await provider.aclose()
        assert len(requests) == 1


@pytest.mark.parametrize('name', ['anthropic', 'bedrock'])
async def test_empty_truncated_text_retains_cutoff_precedence(name, monkeypatch):
    async with native_endpoint(name, [''], 'max_tokens') as (endpoint, requests):
        provider = native_provider(name, monkeypatch)
        try:
            with pytest.raises(ProviderTruncatedError):
                await provider.send_message([ChatMessage(content='owned prompt')], native_config(name, endpoint, True))
        finally:
            await provider.aclose()
        assert len(requests) == 1


@pytest.mark.parametrize('name', ['anthropic', 'bedrock'])
async def test_actual_b13_consumer_keeps_all_empty_native_responses_unscored(name, monkeypatch):
    async with native_endpoint(name, ['']) as (endpoint, requests):
        provider = native_provider(name, monkeypatch)
        fixture = load_fixture('ifixai/fixtures/examples/customer_support.yaml')
        fixture = fixture.model_copy(update={'users': fixture.users[:1], 'tools': fixture.tools[:1], 'governance': None})
        result = await run_single('B13', provider, fixture, api_key='owned-fixture', endpoint=endpoint,
                                  model='owned', timeout=3, max_retries=0,
                                  judge_config=JudgeConfig(provider='mock'), pipeline_config=EvaluationPipelineConfig())
        assert requests
        assert result.status == TestStatus.INCONCLUSIVE
        assert result.insufficient_evidence
        assert not result.passing
        assert not result.evidence
