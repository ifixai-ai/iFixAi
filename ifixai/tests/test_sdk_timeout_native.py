"""Exercise SDK exception classification over owned loopback HTTP sockets."""

import asyncio
import importlib
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderConnectionError, ProviderTimeoutError

ADAPTERS = [
    ("openai", "OpenAIProvider", "openai"),
    ("azure", "AzureOpenAIProvider", "openai"),
    ("atlascloud", "AtlasCloudProvider", "openai"),
    ("openrouter", "OpenRouterProvider", "openai"),
    ("orcarouter", "OrcaRouterProvider", "openai"),
    ("requesty", "RequestyProvider", "openai"),
    ("cloudflare", "CloudflareAIGatewayProvider", "openai"),
    ("anthropic", "AnthropicProvider", "anthropic"),
]


def sdk_adapter(spec):
    module_name, class_name, sdk_name = spec
    sdk = pytest.importorskip(sdk_name)
    module = importlib.import_module(f"ifixai.providers.{module_name}")
    return getattr(module, class_name), sdk


@contextmanager
def owned_http_server(stall=True):
    received = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.set()
            if stall:
                release.wait(10)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def config(endpoint):
    return ProviderConfig(
        provider="owned-sdk-test",
        endpoint=endpoint,
        api_key="synthetic-local-key",
        model="owned-test-model",
        timeout=1,
        max_retries=0,
    )


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda spec: spec[1])
def test_real_sdk_timeout_is_classified_as_timeout(adapter):
    adapter, sdk = sdk_adapter(adapter)
    with owned_http_server() as (endpoint, received):

        async def exercise():
            provider = adapter()
            try:
                with pytest.raises(ProviderTimeoutError) as caught:
                    await provider.send_message(
                        [ChatMessage(role="user", content="Owned timeout probe")],
                        config(endpoint),
                    )
                assert isinstance(caught.value.__cause__, sdk.APITimeoutError)
            finally:
                await provider.aclose()

        asyncio.run(exercise())
        assert received.is_set()


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda spec: spec[1])
def test_connection_close_remains_a_connection_error(adapter):
    adapter, sdk = sdk_adapter(adapter)
    with owned_http_server(stall=False) as (endpoint, received):

        async def exercise():
            provider = adapter()
            try:
                with pytest.raises(ProviderConnectionError) as caught:
                    await provider.send_message(
                        [ChatMessage(role="user", content="Owned connection probe")],
                        config(endpoint),
                    )
                assert isinstance(caught.value.__cause__, sdk.APIConnectionError)
                assert not isinstance(caught.value.__cause__, sdk.APITimeoutError)
            finally:
                await provider.aclose()

        asyncio.run(exercise())
        assert received.is_set()


@pytest.mark.parametrize("spec", [*ADAPTERS[:-1],
    ("huggingface", "HuggingFaceProvider", "huggingface_hub"),
], ids=lambda spec: spec[1])
@pytest.mark.parametrize("text", ["", "partial reply"])
@pytest.mark.parametrize("code", [429, 503])
def test_native_sdk_embedded_completion_errors_keep_their_identity(monkeypatch, spec, text, code):
    import json

    from ifixai.providers.base import (
        ProviderOverloadedError,
        ProviderRateLimitError,
        is_fatal_provider_error,
    )

    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    adapter, _sdk = sdk_adapter(spec)
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append(self.path)
            data = {"id": "owned-response", "object": "chat.completion", "created": 1, "model": "gpt-4o",
                    "choices": [{"index": 0, "finish_reason": "error", "error": {"code": code, "message": "rate limit exceeded" if code == 429 else "owned gateway unavailable"},
                                 "message": {"role": "assistant", "content": text}}]}
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def exercise():
        provider = adapter()
        config = ProviderConfig(provider=spec[0], api_key="owned-key", model="gpt-4o",
                                endpoint=f"http://127.0.0.1:{server.server_port}/v1", max_retries=0)
        try:
            expected = ProviderRateLimitError if code == 429 else ProviderOverloadedError
            with pytest.raises(expected) as caught:
                await provider.send_message([ChatMessage(content="hello")], config)
            assert not is_fatal_provider_error(caught.value)
            assert caught.value.provider == spec[0]
        finally:
            await provider.aclose()
    try:
        asyncio.run(exercise())
        assert len(seen) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
