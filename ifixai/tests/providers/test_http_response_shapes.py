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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 500, 502, 503, 504, 400])
async def test_http_gateway_failures_follow_retry_contract(monkeypatch, status):
    from ifixai.providers.base import ProviderOverloadedError, is_fatal_provider_error

    seen = []
    async def chat(request):
        seen.append(await request.json())
        if len(seen) == 1:
            return aiohttp.web.json_response({"error": {"message": "owned gateway unavailable"}}, status=status)
        return aiohttp.web.json_response({"choices": [{"message": {"content": "recovered reply"}}]})

    async def no_delay(_seconds):
        pass

    monkeypatch.setattr("ifixai.providers.http.asyncio.sleep", no_delay)
    app = aiohttp.web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1"
    provider = HttpProvider()
    try:
        config = ProviderConfig(provider="http", endpoint=url, max_retries=1)
        if status == 400:
            with pytest.raises(ProviderResponseError):
                await provider.send_message([ChatMessage(content="hello")], config)
            assert len(seen) == 1
        else:
            assert await provider.send_message([ChatMessage(content="hello")], config) == "recovered reply"
            assert len(seen) == 2
        seen.clear()
        config.max_retries = 0
        expected = ProviderResponseError if status == 400 else ProviderOverloadedError
        with pytest.raises(expected) as caught:
            await provider.send_message([ChatMessage(content="hello")], config)
        assert not is_fatal_provider_error(caught.value)
        assert len(seen) == 1
    finally:
        await provider.aclose()
        await runner.cleanup()
