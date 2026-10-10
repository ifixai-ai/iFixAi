import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig

adapter = pytest.importorskip("ifixai.providers.anthropic")
AnthropicProvider = adapter.AnthropicProvider


@pytest.mark.asyncio
async def test_anthropic_request_preserves_every_system_instruction():
    requests = []

    async def reply(request):
        requests.append(await request.json())
        return web.json_response({'id': 'msg_owned', 'type': 'message', 'role': 'assistant', 'model': 'owned-model', 'content': [{'type': 'text', 'text': 'owned reply'}], 'stop_reason': 'end_turn', 'stop_sequence': None, 'usage': {'input_tokens': 1, 'output_tokens': 1}})

    app = web.Application()
    app.router.add_post('/v1/messages', reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    provider = AnthropicProvider()
    endpoint = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    try:
        answer = await provider.send_message([ChatMessage(role='system', content='First instruction'), ChatMessage(role='system', content='Second instruction'), ChatMessage(content='hello')], ProviderConfig(provider='anthropic', endpoint=endpoint, model='owned-model', api_key='owned-key', max_retries=0))
        assert answer == 'owned reply'
        assert requests[0]['system'] == 'First instruction\nSecond instruction'
        assert requests[0]['messages'] == [{'role': 'user', 'content': 'hello'}]
    finally:
        await provider.aclose()
        await runner.cleanup()
