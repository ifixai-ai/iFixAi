from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderTruncatedError,
)
from ifixai.providers.http import HttpProvider


@asynccontextmanager
async def endpoint(choice):
    async def invoke(request):
        return web.json_response({'choices': [choice]})
    app = web.Application()
    app.router.add_post('/chat/completions', invoke)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    try:
        yield f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('code,error', [(429, ProviderRateLimitError), (503, ProviderOverloadedError), (400, ProviderTruncatedError)])
async def test_http200_upstream_abort_is_not_a_model_answer(code, error):
    choice = {'message': {'content': 'partial response'}, 'finish_reason': 'error', 'error': {'code': code, 'message': 'owned upstream failure'}}
    provider = HttpProvider()
    try:
        async with endpoint(choice) as url:
            with pytest.raises(error):
                await provider.send_message([ChatMessage(content='hello')], ProviderConfig(provider='http', endpoint=url, max_retries=0))
    finally:
        await provider.aclose()
