import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderTruncatedError
from ifixai.providers.http import HttpProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["partial verdict", ""])
async def test_native_http_judge_rejects_length_cutoff(content):
    async def reply(request):
        return web.json_response(
            {"choices": [{"finish_reason": "length", "message": {"content": content}}]}
        )

    app = web.Application()
    app.router.add_post("/chat/completions", reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    endpoint = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
    provider = HttpProvider()
    try:
        with pytest.raises(ProviderTruncatedError):
            await provider.send_message(
                [ChatMessage(role="user", content="judge")],
                ProviderConfig(
                    provider="http",
                    endpoint=endpoint,
                    max_retries=0,
                    reject_truncated=True,
                ),
            )
    finally:
        await provider.aclose()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_native_http_preserves_truncated_sut_text():
    async def reply(request):
        return web.json_response(
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "SUT partial response"},
                    }
                ]
            }
        )

    app = web.Application()
    app.router.add_post("/chat/completions", reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    endpoint = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
    provider = HttpProvider()
    try:
        assert (
            await provider.send_message(
                [ChatMessage(role="user", content="probe")],
                ProviderConfig(provider="http", endpoint=endpoint, max_retries=0),
            )
            == "SUT partial response"
        )
    finally:
        await provider.aclose()
        await runner.cleanup()
