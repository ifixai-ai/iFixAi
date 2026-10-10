import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderRateLimitError, is_fatal_provider_error
from ifixai.providers.minimax import MiniMaxProvider


@pytest.mark.asyncio
async def test_minimax_exhausted_quota_is_fatal_without_retrying(monkeypatch):
    requests = []

    async def no_sleep(*args):
        return None

    async def reply(request):
        requests.append(await request.json())
        return web.json_response({'error': {'code': 'insufficient_quota', 'message': 'owned quota exhausted'}}, status=429)

    monkeypatch.setattr('ifixai.providers.minimax.asyncio.sleep', no_sleep)
    app = web.Application()
    app.router.add_post('/chat/completions', reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    endpoint = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    provider = MiniMaxProvider()
    try:
        with pytest.raises(ProviderRateLimitError) as caught:
            await provider.send_message([ChatMessage(content='hello')], ProviderConfig(provider='minimax', endpoint=endpoint, max_retries=1))
        assert is_fatal_provider_error(caught.value)
        assert len(requests) == 1
    finally:
        await provider.aclose()
        await runner.cleanup()
