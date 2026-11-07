"""A 403 the gateway sends for flagged content fails one probe; it is not a key refusal."""

import asyncio

import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.judge.evaluator import judge_refusal
from ifixai.providers.base import (
    ProviderAuthError,
    ProviderResponseError,
    is_fatal_provider_error,
)
from ifixai.providers.http import HttpProvider
from ifixai.providers.langchain import LangChainProvider

_FLAGGED = {"error": {"code": 403, "message": (
    "openai/gpt-4o requires moderation on OpenRouter. Your input was flagged for "
    "\"violence\". No credits were charged.")}}
_KEY_REJECTED = {"error": {"code": 403, "message": "Invalid API key provided."}}


async def _send(provider_cls, provider_name, path, body):
    async def reply(_request):
        return web.json_response(body, status=403)

    app = web.Application()
    app.router.add_post(path, reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}" + ("/v1" if provider_name == "http" else "")
    provider = provider_cls()
    try:
        await provider.send_message(
            [ChatMessage(role="user", content="adversarial probe")],
            ProviderConfig(provider=provider_name, endpoint=endpoint, api_key="owned-key",
                           max_retries=0, model="owned"),
        )
    except Exception as exc:  # noqa: BLE001 - the classification is what is asserted
        return exc
    finally:
        await provider.aclose()
        await runner.cleanup()
    raise AssertionError("expected the 403 to raise")


_ADAPTERS = [
    (HttpProvider, "http", "/v1/chat/completions"),
    (LangChainProvider, "langchain", "/invoke"),
]


@pytest.mark.parametrize("provider_cls, name, path", _ADAPTERS, ids=["http", "langchain"])
def test_content_flag_403_fails_only_the_probe(provider_cls, name, path):
    exc = asyncio.run(_send(provider_cls, name, path, _FLAGGED))
    assert isinstance(exc, ProviderResponseError)
    assert "flagged" in str(exc)
    assert not is_fatal_provider_error(exc)
    assert judge_refusal(exc) is None


@pytest.mark.parametrize("provider_cls, name, path", _ADAPTERS, ids=["http", "langchain"])
def test_key_rejection_403_still_stops_the_run(provider_cls, name, path):
    exc = asyncio.run(_send(provider_cls, name, path, _KEY_REJECTED))
    assert isinstance(exc, ProviderAuthError)
    assert is_fatal_provider_error(exc)
