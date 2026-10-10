"""Native SDK requests must learn temperature support per endpoint and model."""
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from ifixai.tests.test_anthropic_temperature_native import (
    ACCEPTS,
    REJECTS,
    AnthropicProvider,
    FakeMessagesApi,
    adapter,
    send,
)


@contextmanager
def local_api() -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMessagesApi)
    server.bodies = []
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize("same_provider", [True, False])
async def test_temperature_rejection_at_one_endpoint_does_not_disable_another(
    monkeypatch, same_provider,
):
    monkeypatch.setattr(adapter, "_NO_TEMPERATURE_MODELS", set())
    # Same model name resolves to different capabilities behind the two hosts.
    with local_api() as rejects, local_api() as accepts:
        original_handler = accepts.RequestHandlerClass
        class AcceptAll(original_handler):
            def do_POST(self):
                import json

                from ifixai.tests.test_anthropic_temperature_native import message
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.bodies.append(body)
                self.reply(200, message(body["model"]))
        accepts.RequestHandlerClass = AcceptAll
        first = AnthropicProvider()
        second = first if same_provider else AnthropicProvider()
        try:
            assert await send(first, rejects, REJECTS, .25) == "fake reply"
            assert await send(second, accepts, REJECTS, .75) == "fake reply"
            assert await send(second, rejects, REJECTS, .25) == "fake reply"
            assert await send(first, accepts, ACCEPTS, .5) == "fake reply"
        finally:
            await first.aclose()
            if second is not first:
                await second.aclose()
        assert [b.get("temperature") for b in rejects.bodies] == [.25, None, None]
        assert [b.get("temperature") for b in accepts.bodies] == [.75, .5]


async def test_default_api_and_equivalent_trailing_slash_share_learning(monkeypatch):
    import json

    import anthropic
    from anthropic import _base_client
    httpx = getattr(_base_client, "httpx2", None) or _base_client.httpx
    from ifixai.tests.test_anthropic_temperature_native import error, message

    monkeypatch.setattr(adapter, "_NO_TEMPERATURE_MODELS", set())
    bodies = []
    def respond(request):
        assert request.url.host == "api.anthropic.com"
        body = json.loads(request.content)
        bodies.append(body)
        if body["model"] == REJECTS and "temperature" in body:
            return httpx.Response(400, json=error("temperature not supported"))
        return httpx.Response(200, json=message(body["model"]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        async with anthropic.AsyncAnthropic(api_key="owned-fixture", http_client=transport) as default:
            await adapter._create_message(default, {"model": REJECTS, "max_tokens": 4, "messages": [{"role": "user", "content": "owned"}]}, .25)
            await adapter._create_message(default, {"model": ACCEPTS, "max_tokens": 4, "messages": [{"role": "user", "content": "owned"}]}, .75)
            async with anthropic.AsyncAnthropic(api_key="owned-fixture", base_url="https://api.anthropic.com/", http_client=transport) as equivalent:
                await adapter._create_message(equivalent, {"model": REJECTS, "max_tokens": 4, "messages": [{"role": "user", "content": "owned"}]}, .25)
    assert [body.get("temperature") for body in bodies] == [.25, None, .75, None]
