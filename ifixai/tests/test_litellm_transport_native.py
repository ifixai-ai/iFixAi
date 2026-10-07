"""Actual optional LiteLLM SDK exercised through owned HTTP sockets."""
import asyncio
import importlib
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    is_fatal_provider_error,
)


@contextmanager
def owned_server(status=200, stall=False, disconnect=False):
    received = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.set()
            if disconnect:
                return
            if stall:
                release.wait(10)
                return
            payload = {"id": "owned-response", "object": "chat.completion",
                       "created": 1, "model": "gpt-4o",
                       "choices": [{"index": 0, "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": "owned reply"}}]}
            if status != 200:
                payload = {"error": {"message": "Rate limit exceeded" if status == 429 else "Owned HTTP rejection",
                                     "type": "rate_limit_error" if status == 429 else "api_error",
                                     "code": "rate_limit_exceeded" if status == 429 else str(status)}}
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", received
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)



def send(endpoint, monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    pytest.importorskip("litellm")
    provider_class = importlib.import_module("ifixai.providers.litellm").LiteLLMProvider
    return asyncio.run(provider_class().send_message(
        [ChatMessage(role="user", content="Owned transport probe")],
        ProviderConfig(provider="litellm", model="openai/gpt-4o", endpoint=endpoint,
                       api_key="synthetic-local-key", timeout=1, max_retries=0),
    ))


@pytest.mark.parametrize("status,error", [
    (401, ProviderAuthError), (403, ProviderAuthError), (429, ProviderRateLimitError),
    (500, ProviderOverloadedError), (502, ProviderOverloadedError),
    (503, ProviderOverloadedError), (408, ProviderTimeoutError),
    (504, ProviderTimeoutError),
    (400, ProviderResponseError),
])
def test_real_sdk_http_errors_preserve_shared_contract(status, error, monkeypatch):
    with owned_server(status=status) as (url, received):
        with pytest.raises(error) as caught:
            send(url, monkeypatch)
        assert received.is_set()
        assert caught.value.provider == "litellm"
        assert caught.value.endpoint == url
        if status == 429:
            assert not is_fatal_provider_error(caught.value)


def test_real_sdk_read_timeout_is_typed(monkeypatch):
    with owned_server(stall=True) as (url, received):
        with pytest.raises(ProviderTimeoutError):
            send(url, monkeypatch)
        assert received.is_set()


def test_real_sdk_connection_drop_remains_recoverable(monkeypatch):
    with owned_server(disconnect=True) as (url, received):
        with pytest.raises((ProviderConnectionError, ProviderOverloadedError)) as caught:
            send(url, monkeypatch)
        assert received.is_set()
        assert not is_fatal_provider_error(caught.value)


def test_real_sdk_success_control(monkeypatch):
    with owned_server() as (url, received):
        assert send(url, monkeypatch) == "owned reply"
        assert received.is_set()


def test_sdk_without_optional_permission_export_keeps_transient_errors(monkeypatch):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    sdk = pytest.importorskip("litellm")
    monkeypatch.delattr(sdk, "PermissionDeniedError", raising=False)
    with owned_server(status=503) as (url, received):
        with pytest.raises(ProviderOverloadedError) as caught:
            send(url, monkeypatch)
        assert received.is_set()
        assert not is_fatal_provider_error(caught.value)


@pytest.mark.parametrize("retries", [0, 1])
def test_litellm_native_request_obeys_retry_budget(monkeypatch, retries):
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    sdk = pytest.importorskip("litellm")
    from ifixai.providers.litellm import LiteLLMProvider

    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append(self.path)
            if len(seen) == 1:
                status = 500
                data = {"error": {"message": "owned transient server failure", "type": "server_error"}}
            else:
                status = 200
                data = {"id": "owned-response", "object": "chat.completion", "created": 1, "model": "gpt-4o",
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "recovered reply"}}]}
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass

    # A process-wide SDK default must not override the requested budget.
    monkeypatch.setattr(sdk, "num_retries", 0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def exercise():
        provider = LiteLLMProvider()
        config = ProviderConfig(provider="litellm", model="openai/gpt-4o", api_key="owned-key",
                                endpoint=f"http://127.0.0.1:{server.server_port}/v1", max_retries=retries)
        if retries:
            assert await provider.send_message([ChatMessage(content="hello")], config) == "recovered reply"
        else:
            with pytest.raises(ProviderOverloadedError):
                await provider.send_message([ChatMessage(content="hello")], config)
    try:
        asyncio.run(exercise())
        assert len(seen) == retries + 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
