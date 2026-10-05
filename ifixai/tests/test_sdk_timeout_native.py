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
