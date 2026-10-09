"""Actual aiohttp /invoke requests preserve provider error contracts."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.connection import test_connection as probe_connection
from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from ifixai.providers.langchain import LangChainProvider


@asynccontextmanager
async def endpoint(status=200, stall=False):
    release = asyncio.Event()
    requests = []

    async def invoke(request):
        requests.append(await request.json())
        if stall:
            await release.wait()
        return web.json_response({"output": "owned reply"}, status=status)

    app = web.Application()
    app.router.add_post("/invoke", invoke)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
    try:
        yield url, requests
    finally:
        release.set()
        await runner.cleanup()


def config(url):
    return ProviderConfig(provider="langchain", endpoint=url, timeout=1, max_retries=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error", [
    (401, ProviderAuthError), (403, ProviderAuthError),
    (429, ProviderRateLimitError), (408, ProviderOverloadedError),
    (500, ProviderOverloadedError), (502, ProviderOverloadedError),
    (503, ProviderOverloadedError), (504, ProviderOverloadedError),
    (400, ProviderResponseError), (404, ProviderResponseError),
])
async def test_http_rejections_are_typed_provider_errors(status, error):
    async with endpoint(status=status) as (url, requests):
        with pytest.raises(error) as caught:
            await LangChainProvider().send_message(
                [ChatMessage(role="user", content="owned prompt")], config(url)
            )
        assert caught.value.provider == "langchain"
        assert caught.value.endpoint == url + "/invoke"
        assert str(status) in caught.value.details
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_total_request_timeout_is_typed():
    async with endpoint(stall=True) as (url, requests):
        with pytest.raises(ProviderTimeoutError):
            await LangChainProvider().send_message(
                [ChatMessage(role="user", content="owned prompt")], config(url)
            )
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_connection_probe_reports_authentication_failure():
    async with endpoint(status=401) as (url, _requests):
        result = await probe_connection(LangChainProvider(), config(url))
        assert not result.success
        assert result.error_message.startswith("Authentication failed")


@pytest.mark.asyncio
async def test_success_retains_documented_wire_format():
    async with endpoint() as (url, requests):
        messages = [ChatMessage(role="system", content="instruction"),
                    ChatMessage(role="user", content="owned prompt")]
        assert await LangChainProvider().send_message(messages, config(url)) == "owned reply"
        assert requests == [{"input": {"messages": [m.model_dump() for m in messages]},
                            "config": {"configurable": {"temperature": 0.0}}}]


@pytest.mark.asyncio
async def test_disconnected_endpoint_remains_connection_error():
    async with endpoint() as (url, _requests):
        pass
    with pytest.raises(ProviderConnectionError):
        await LangChainProvider().send_message(
            [ChatMessage(role="user", content="owned prompt")], config(url)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("auth,key,custom,required", [
    ("bearer", "owned-key", {}, {"Authorization": "Bearer owned-key"}),
    ("basic", "owned:password", {}, {"Authorization": "Basic b3duZWQ6cGFzc3dvcmQ="}),
    ("api_key", "owned-key", {}, {"X-API-Key": "owned-key"}),
    ("none", "", {"X-Owned-Token": "owned-token"}, {"X-Owned-Token": "owned-token"}),
])
async def test_langserve_authenticated_invoke_uses_configured_headers(monkeypatch, auth, key, custom, required):
    monkeypatch.delenv("IFIXAI_EXTRA_HEADERS", raising=False)
    seen = []
    async def invoke(request):
        seen.append(dict(request.headers))
        if any(request.headers.get(name) != value for name, value in required.items()):
            return web.json_response({"detail": "owned authentication required"}, status=401)
        return web.json_response({"output": "owned authenticated reply"})

    app = web.Application()
    app.router.add_post("/invoke", invoke)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        config = ProviderConfig(provider="langchain", endpoint=url, auth_method=auth,
                                api_key=key, extra_headers=custom, max_retries=0)
        assert await LangChainProvider().send_message([ChatMessage(content="hello")], config) == "owned authenticated reply"
        assert len(seen) == 1
    finally:
        await runner.cleanup()
