from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderEmptyContentError, ProviderResponseError
from ifixai.providers.langchain import LangChainProvider


@asynccontextmanager
async def endpoint(payload):
    async def invoke(request):
        return web.json_response(payload)
    app = web.Application()
    app.router.add_post('/invoke', invoke)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    try:
        yield f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('payload,error', [({}, ProviderResponseError), ({'output': None}, ProviderResponseError), ({'output': {'content': None}}, ProviderResponseError), ({'output': {'content': 42}}, ProviderResponseError), ({'output': ''}, ProviderEmptyContentError), ({'output': {'content': ''}}, ProviderEmptyContentError)])
async def test_invalid_or_empty_output_does_not_become_model_answer(payload, error):
    async with endpoint(payload) as url:
        with pytest.raises(error):
            await LangChainProvider().send_message([ChatMessage(content='hello')], ProviderConfig(provider='langchain', endpoint=url))
