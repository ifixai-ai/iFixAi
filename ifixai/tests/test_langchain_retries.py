"""LangServe calls retry transient failures and type malformed replies, like the HTTP adapter."""

import asyncio

import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers import langchain
from ifixai.providers.base import ProviderOverloadedError, ProviderResponseError
from ifixai.providers.langchain import LangChainProvider


async def _call(responses, max_retries):
    seen = []

    async def invoke(_request):
        seen.append(1)
        status, body, content_type = responses[min(len(seen), len(responses)) - 1]
        return web.Response(status=status, text=body, content_type=content_type)

    app = web.Application()
    app.router.add_post("/invoke", invoke)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        reply = await LangChainProvider().send_message(
            [ChatMessage(role="user", content="hello")],
            ProviderConfig(provider="langchain", endpoint=f"http://127.0.0.1:{port}",
                           timeout=5, max_retries=max_retries),
        )
        return reply, len(seen)
    finally:
        await runner.cleanup()


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(langchain.asyncio, "sleep", no_sleep)


_OK = (200, '{"output": "owned reply"}', "application/json")
_UNAVAILABLE = (503, "Service Unavailable", "text/plain")


def test_transient_failure_is_retried():
    reply, requests = asyncio.run(_call([_UNAVAILABLE, _OK], max_retries=3))
    assert reply == "owned reply"
    assert requests == 2


def test_retries_are_bounded_by_max_retries():
    with pytest.raises(ProviderOverloadedError):
        asyncio.run(_call([_UNAVAILABLE], max_retries=2))


def test_malformed_json_is_a_typed_response_error():
    with pytest.raises(ProviderResponseError, match="not valid JSON"):
        asyncio.run(_call([(200, '{"output": ', "application/json")], max_retries=0))
