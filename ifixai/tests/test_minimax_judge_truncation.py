from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderTruncatedError
from ifixai.providers.minimax import MiniMaxProvider


@asynccontextmanager
async def endpoint(style, text):
    async def reply(request):
        if style == 'messages':
            return web.json_response({'content': [{'type': 'text', 'text': text}], 'stop_reason': 'max_tokens'})
        return web.json_response({'choices': [{'message': {'content': text}, 'finish_reason': 'length'}]})
    app = web.Application()
    path = '/anthropic/v1/messages' if style == 'messages' else '/chat/completions'
    app.router.add_post(path, reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    try:
        yield f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}' + ('/anthropic' if style == 'messages' else '')
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('style', ['messages', 'chat_completions'])
@pytest.mark.parametrize('reject', [True, False])
async def test_minimax_honors_judge_cutoff_flag_without_dropping_sut_reply(style, reject):
    provider = MiniMaxProvider()
    try:
        async with endpoint(style, 'partial verdict') as url:
            config = ProviderConfig(provider='minimax', endpoint=url, reject_truncated=reject)
            if reject:
                with pytest.raises(ProviderTruncatedError):
                    await provider.send_message([ChatMessage(content='grade this')], config)
            else:
                assert await provider.send_message([ChatMessage(content='grade this')], config) == 'partial verdict'
    finally:
        await provider.aclose()
