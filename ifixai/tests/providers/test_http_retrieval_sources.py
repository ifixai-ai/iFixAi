"""Retrieval evidence must come from the requested query, not a prior chat."""

import aiohttp.web
import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.http import HttpProvider


@pytest.mark.asyncio
async def test_failed_retrieval_does_not_reuse_prior_chat_sources():
    async def chat(_request):
        return aiohttp.web.json_response({
            "choices": [{"message": {"content": "first answer"}}],
            "sources": [{"document_uri": "old-document"}],
        })

    async def retrieve(_request):
        return aiohttp.web.Response(status=404)

    app = aiohttp.web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_post("/v1/retrieve", retrieve)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    provider = HttpProvider()
    config = ProviderConfig(provider="http", endpoint=f"http://127.0.0.1:{port}/v1")
    try:
        assert await provider.send_message([ChatMessage(content="first query")], config) == "first answer"
        assert await provider.retrieve_sources("unrelated query", config) is None
    finally:
        await provider.aclose()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_successful_retrieval_returns_requested_sources():
    async def retrieve(request):
        body = await request.json()
        return aiohttp.web.json_response({"sources": [{"document_uri": body["query"]}]})

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
        sources = await provider.retrieve_sources("requested-document", config)
        assert sources is not None
        assert [source.source_id for source in sources] == ["requested-document"]
    finally:
        await provider.aclose()
        await runner.cleanup()
