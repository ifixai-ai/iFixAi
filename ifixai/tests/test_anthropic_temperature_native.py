"""Temperature handling in the Anthropic adapter, over the real SDK and a loopback fake API."""

import asyncio
import json
import logging
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderResponseError

anthropic = pytest.importorskip("anthropic")
adapter = pytest.importorskip("ifixai.providers.anthropic")
AnthropicProvider = adapter.AnthropicProvider

ACCEPTS = "claude-sonnet-4-6"
REJECTS = "claude-opus-4-7"
REJECTS_CAPITALISED = "claude-sonnet-5"
UNKNOWN = "claude-nope"
TEMPERATURE_400 = "temperature is deprecated for this model."
CAPITALISED_400 = "Temperature is not supported for this model."
RANGE_400 = "temperature: range: 0..1"
MODEL_400 = "model: claude-nope is not a valid model"


class FakeMessagesApi(BaseHTTPRequestHandler):
    """Like the real API: temperature above 1 is a 400 for every model, REJECTS and
    REJECTS_CAPITALISED answer 400 to any temperature, UNKNOWN always 400s."""

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.bodies.append(body)
        if body["model"] == UNKNOWN:
            self.reply(400, error(MODEL_400))
        elif body.get("temperature", 0) > 1:
            self.reply(400, error(RANGE_400))
        elif body["model"] == REJECTS and "temperature" in body:
            self.reply(400, error(TEMPERATURE_400))
        elif body["model"] == REJECTS_CAPITALISED and "temperature" in body:
            self.reply(400, error(CAPITALISED_400))
        else:
            self.reply(200, message(body["model"]))

    def reply(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args: object) -> None:
        pass


def error(text: str) -> dict:
    return {"type": "error", "error": {"type": "invalid_request_error", "message": text}}


def message(model: str) -> dict:
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": "fake reply"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


@pytest.fixture
def api(monkeypatch) -> Iterator[ThreadingHTTPServer]:
    monkeypatch.setattr(adapter, "_NO_TEMPERATURE_MODELS", set())
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMessagesApi)
    server.bodies = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(2)


async def send(
    provider: AnthropicProvider,
    api: ThreadingHTTPServer,
    model: str,
    temperature: float = 0.0,
) -> str:
    return await provider.send_message(
        [ChatMessage(role="user", content="hi")],
        ProviderConfig(
            provider="anthropic",
            endpoint=f"http://127.0.0.1:{api.server_port}",
            api_key="synthetic-local-key",
            model=model,
            timeout=5,
            max_retries=0,
            temperature=temperature,
        ),
    )


async def test_model_that_accepts_temperature_gets_zero(api):
    provider = AnthropicProvider()
    try:
        assert await send(provider, api, ACCEPTS) == "fake reply"
    finally:
        await provider.aclose()
    assert [b["temperature"] for b in api.bodies] == [0.0]


async def test_model_that_rejects_temperature_retries_once_then_skips_it(api, caplog):
    caplog.set_level(logging.WARNING, logger="ifixai.providers.anthropic")
    sut, judge = AnthropicProvider(), AnthropicProvider()
    try:
        assert await send(sut, api, REJECTS) == "fake reply"
        assert await send(sut, api, REJECTS) == "fake reply"
        assert await send(judge, api, REJECTS) == "fake reply"
    finally:
        await sut.aclose()
        await judge.aclose()
    assert ["temperature" in b for b in api.bodies] == [True, False, False, False]
    assert [r.getMessage() for r in caplog.records] == [
        f"{REJECTS} rejects temperature; it runs at the model's default temperature"
    ]


async def test_concurrent_first_calls_warn_once(api, caplog):
    caplog.set_level(logging.WARNING, logger="ifixai.providers.anthropic")
    provider = AnthropicProvider()
    try:
        replies = await asyncio.gather(send(provider, api, REJECTS), send(provider, api, REJECTS))
    finally:
        await provider.aclose()
    assert replies == ["fake reply", "fake reply"]
    assert sorted("temperature" in b for b in api.bodies) == [False, False, True, True]
    assert len(caplog.records) == 1


async def test_unrelated_400_raises_without_retry(api):
    provider = AnthropicProvider()
    try:
        with pytest.raises(ProviderResponseError, match=MODEL_400) as caught:
            await send(provider, api, UNKNOWN)
    finally:
        await provider.aclose()
    assert isinstance(caught.value.__cause__, anthropic.BadRequestError)
    assert len(api.bodies) == 1


async def test_capitalised_temperature_400_takes_the_retry_path(api):
    provider = AnthropicProvider()
    try:
        assert await send(provider, api, REJECTS_CAPITALISED) == "fake reply"
        assert await send(provider, api, REJECTS_CAPITALISED) == "fake reply"
    finally:
        await provider.aclose()
    assert ["temperature" in b for b in api.bodies] == [True, False, False]
    assert adapter._NO_TEMPERATURE_MODELS == {
        (f"http://127.0.0.1:{api.server_port}", REJECTS_CAPITALISED)
    }


async def test_out_of_range_temperature_raises_and_caches_nothing(api):
    provider = AnthropicProvider()
    try:
        with pytest.raises(ProviderResponseError, match=re.escape(RANGE_400)) as caught:
            await send(provider, api, ACCEPTS, temperature=1.5)
    finally:
        await provider.aclose()
    assert isinstance(caught.value.__cause__, anthropic.BadRequestError)
    assert [b["temperature"] for b in api.bodies] == [1.5]
    assert adapter._NO_TEMPERATURE_MODELS == set()
