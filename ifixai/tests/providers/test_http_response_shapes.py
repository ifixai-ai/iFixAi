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
@pytest.mark.parametrize("detail,fatal,attempts", [
    ("insufficient_quota", True, 1),
    ("insufficient credits", True, 1),
    ("payment required", True, 1),
    ("rate limit exceeded", False, 2),
    ("You exceeded your current quota, please check your plan and billing details", False, 2),
    ("Token rate limit exceeded. Retry after 60 seconds: https://aka.ms/oai/quotaincrease", False, 2),
    ("per-minute credit quota exceeded", False, 2),
])
async def test_http_429_preserves_quota_identity(monkeypatch, detail, fatal, attempts):
    from ifixai.providers.base import ProviderRateLimitError, is_fatal_provider_error

    seen = []
    async def chat(request):
        seen.append(await request.json())
        return aiohttp.web.json_response({"error": {"message": detail + " sk-" + "A" * 24}}, status=429)

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
        with pytest.raises(ProviderRateLimitError) as caught:
            await provider.send_message([ChatMessage(content="hello")], ProviderConfig(provider="http", endpoint=url, max_retries=1))
        assert is_fatal_provider_error(caught.value) is fatal
        assert detail in caught.value.details
        assert "sk-" + "A" * 24 not in caught.value.details
        assert len(seen) == attempts
    finally:
        await provider.aclose()
        await runner.cleanup()


@pytest.mark.parametrize("detail,fatal", [
    ("insufficient_quota", True), ("insufficient credits", True),
    ("payment required", True), ("billing limit reached", False),
    ("rate limit exceeded", False), ("HTTP 429, retry later", False),
    ("You exceeded your current quota, please check your plan and billing details", False),
    ("Token rate limit exceeded. Retry after 60 seconds: https://aka.ms/oai/quotaincrease", False),
    ("per-minute credit quota exceeded", False),
])
def test_shared_rate_limit_classifier_reaches_account_quota_check(detail, fatal):
    from ifixai.providers.base import (
        ProviderConnectionError,
        ProviderRateLimitError,
        is_fatal_provider_error,
    )
    assert is_fatal_provider_error(ProviderRateLimitError(details=detail)) is fatal
    assert not is_fatal_provider_error(ProviderConnectionError(details=detail))


def test_quota_markers_in_provider_metadata_do_not_make_throttling_fatal():
    from ifixai.providers.base import ProviderRateLimitError, is_fatal_provider_error

    error = ProviderRateLimitError(provider="credit-proxy", endpoint="https://owned.invalid/quota", details="rate limit exceeded")
    assert not is_fatal_provider_error(error)
