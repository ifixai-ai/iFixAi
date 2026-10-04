"""Malformed successful HTTP replies should surface as provider errors."""

import aiohttp.web
import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderResponseError
from ifixai.providers.http import HttpProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        [],
        {"choices": "bad"},
        {"choices": [{"message": {"content": ["not", "text"]}}]},
    ],
)
async def test_malformed_chat_reply_is_provider_response_error(reply):
    async def chat(_request):
        if isinstance(reply, str):
            return aiohttp.web.Response(text=reply, content_type="text/plain")
        return aiohttp.web.json_response(reply)

    app = aiohttp.web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    provider = HttpProvider()
    config = ProviderConfig(
        provider="http", endpoint=f"http://127.0.0.1:{port}/v1", max_retries=0
    )
    try:
        with pytest.raises(ProviderResponseError):
            await provider.send_message([ChatMessage(content="hello")], config)
    finally:
        await provider.aclose()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_malformed_retrieval_reply_reports_unavailable():
    async def retrieve(_request):
        return aiohttp.web.json_response([{"document_uri": "wrong shape"}])

    app = aiohttp.web.Application()
    app.router.add_post("/v1/retrieve", retrieve)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    provider = HttpProvider()
    config = ProviderConfig(provider="http", endpoint=f"http://127.0.0.1:{port}/v1")
    try:
        assert await provider.retrieve_sources("hello", config) is None
    finally:
        await provider.aclose()
        await runner.cleanup()
