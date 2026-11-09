"""An error page that echoes the request's auth header must not leak the agent's key."""

import asyncio
import base64
import json

import pytest
from aiohttp import web

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderError
from ifixai.providers.http import HttpProvider
from ifixai.providers.secrets import scrub_secrets

_KEY = "tenant-prod-key-9f8e7d6c5b4a3f2e1d0c"


async def _error_text(auth_method: str) -> str:
    async def echo(request):
        sent = {name: value for name, value in request.headers.items()
                if name.lower() in ("authorization", "x-api-key")}
        raw = "\n".join(f"{name}: {value}" for name, value in sent.items())
        return web.Response(status=502, text=f"{raw}\n{sent}\n{json.dumps(sent)}")

    app = web.Application()
    app.router.add_post("/v1/chat/completions", echo)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    provider = HttpProvider()
    try:
        await provider.send_message(
            [ChatMessage(role="user", content="hi")],
            ProviderConfig(provider="http", endpoint=f"http://127.0.0.1:{port}/v1",
                           api_key=_KEY, auth_method=auth_method, max_retries=0),
        )
    except ProviderError as exc:
        return str(exc)
    finally:
        await provider.aclose()
        await runner.cleanup()
    raise AssertionError("expected the 502 to raise")


@pytest.mark.parametrize("auth_method", ["bearer", "basic", "api_key"])
def test_echoed_credentials_are_redacted(auth_method):
    text = asyncio.run(_error_text(auth_method))
    assert "HTTP 502" in text
    assert _KEY not in text
    assert base64.b64encode(_KEY.encode()).decode() not in text


def test_prose_mentioning_basic_authorization_is_untouched():
    prose = "The basic idea of authorization is simple."
    assert scrub_secrets(prose) == prose
