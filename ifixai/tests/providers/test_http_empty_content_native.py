"""Completed empty HTTP replies use the existing unscorable-output contract."""

from collections import Counter
from contextlib import asynccontextmanager

import aiohttp.web
import pytest

from ifixai.api import run_single
from ifixai.core.types import ChatMessage, ProviderConfig, TestRunResult, TestStatus
from ifixai.providers.base import (
    ProviderEmptyContentError,
    ProviderResponseError,
    ProviderTruncatedError,
)
from ifixai.providers.http import HttpProvider
from ifixai.reporting.health import run_health


def completion(content="", finish_reason="stop"):
    return {
        "choices": [{
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
    }


@asynccontextmanager
async def owned_endpoint(reply):
    requests = []

    async def respond(request):
        requests.append((request.path, await request.json()))
        return aiohttp.web.json_response(reply)

    app = aiohttp.web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    app.router.add_post("/v1/retrieve", respond)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1", requests
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, expected",
    [
        ({}, ProviderResponseError),
        ({"choices": []}, ProviderResponseError),
        ({"choices": [{}]}, ProviderResponseError),
        ({"choices": [{"message": {}}]}, ProviderResponseError),
        (completion(None), ProviderResponseError),
        (completion([]), ProviderResponseError),
        ({"error": {"code": 429, "message": "rate limited"}}, ProviderResponseError),
        ({"choices": [{"message": {"content": ""}, "finish_reason": "error",
                       "error": {"code": 429, "message": "rate limited"}}]}, ProviderResponseError),
        (dict(completion(), error={"code": 429, "message": "rate limited"}), ProviderResponseError),
        (completion("", "length"), ProviderTruncatedError),
        (completion("ordinary"), "ordinary"),
        (completion(), ProviderEmptyContentError),
        (completion("", "ERROR"), ProviderResponseError),
        ({"choices": [{"message": {"content": ""}, "error": {}}]}, ProviderResponseError),
    ],
)
async def test_empty_completion_keeps_other_response_failures_distinct(reply, expected):
    async with owned_endpoint(reply) as (endpoint, requests):
        provider = HttpProvider()
        config = ProviderConfig(
            provider="http", model="owned", endpoint=endpoint,
            max_retries=1, timeout=5, reject_truncated=True,
        )
        try:
            if isinstance(expected, str):
                assert await provider.send_message([ChatMessage(content="hello")], config) == expected
            else:
                with pytest.raises(expected) as caught:
                    await provider.send_message([ChatMessage(content="hello")], config)
                assert type(caught.value) is expected
                assert caught.value.provider == "http"
                assert caught.value.endpoint == endpoint
            assert len(requests) == 1
        finally:
            await provider.aclose()


@pytest.mark.asyncio
async def test_public_inspection_stops_without_false_communication_evidence():
    async with owned_endpoint(completion()) as (endpoint, requests):
        result = await run_single(
            "B06", provider=HttpProvider(), fixture="default", endpoint=endpoint,
            model="owned", timeout=5, max_retries=0,
        )
        assert result.status is TestStatus.INCONCLUSIVE
        assert result.error and "Empty content" in result.error
        assert result.evidence == []
        assert Counter(path for path, _ in requests) == {
            "/v1/retrieve": 1, "/v1/chat/completions": 1,
        }
        health = run_health(TestRunResult(test_results=[result]))
        assert health.unreachable == 0
