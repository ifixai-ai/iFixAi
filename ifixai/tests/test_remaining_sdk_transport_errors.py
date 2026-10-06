"""Normalize real SDK transport faults before the harness classifies them."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderConnectionError, ProviderTimeoutError


@pytest.mark.asyncio
@pytest.mark.parametrize("module,error_name,expected", [
    ("httpx", "ReadTimeout", ProviderTimeoutError),
    ("httpx", "ConnectError", ProviderConnectionError),
    ("requests.exceptions", "ReadTimeout", ProviderTimeoutError),
    ("requests.exceptions", "ConnectionError", ProviderConnectionError),
])
async def test_huggingface_transport_versions_are_normalized(monkeypatch, module, error_name, expected):
    pytest.importorskip("huggingface_hub")
    errors = pytest.importorskip(module)
    from ifixai.providers import huggingface

    def fail(*args):
        raise getattr(errors, error_name)("owned transport failure")

    monkeypatch.setattr(huggingface, "_call_chat_completion", fail)
    with pytest.raises(expected):
        await huggingface.HuggingFaceProvider().send_message([ChatMessage(content="hello")], ProviderConfig(provider="huggingface", model="owned-model", api_key="synthetic-local-key", max_retries=0))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["huggingface", "bedrock"])
async def test_owned_http_disconnect_is_connection_error(monkeypatch, tmp_path, provider_name):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        if provider_name == "huggingface":
            pytest.importorskip("huggingface_hub")
            from ifixai.providers.huggingface import HuggingFaceProvider

            provider = HuggingFaceProvider()
            # Use the existing model-URL route, independently of the endpoint fix.
            model = endpoint + "/v1/chat/completions"
        else:
            boto3 = pytest.importorskip("boto3")
            from botocore.config import Config

            from ifixai.providers.bedrock import BedrockProvider

            session = boto3.Session(aws_access_key_id="synthetic-local-key", aws_secret_access_key="synthetic-local-secret", region_name="us-east-1")
            native_client = session.client("bedrock-runtime", endpoint_url=endpoint, config=Config(retries={"total_max_attempts": 1}))

            class OwnedSession:
                def client(self, **kwargs):
                    assert kwargs["endpoint_url"] == endpoint
                    return native_client

            monkeypatch.setattr(boto3, "Session", lambda **kwargs: OwnedSession())
            provider = BedrockProvider()
            model = "owned-model"
        config = ProviderConfig(provider=provider_name, endpoint=endpoint, model=model, api_key="synthetic-local-key", max_retries=0)
        with pytest.raises(ProviderConnectionError):
            await provider.send_message([ChatMessage(content="hello")], config)
        assert len(seen) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.asyncio
async def test_gemini_native_deadline_is_timeout(monkeypatch):
    pytest.importorskip("google.generativeai")
    from google.api_core.exceptions import DeadlineExceeded
    from google.generativeai import client

    from ifixai.providers import gemini

    class OwnedTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def generate_content(self, request, **kwargs):
            raise DeadlineExceeded("owned transport deadline")

    monkeypatch.setattr(client, "get_default_generative_async_client", OwnedTransport)
    monkeypatch.setattr(gemini, "GenerativeServiceAsyncClient", lambda **kwargs: OwnedTransport(), raising=False)
    with pytest.raises(ProviderTimeoutError):
        await gemini.GeminiProvider().send_message([ChatMessage(content="hello")], ProviderConfig(provider="gemini", api_key="synthetic-local-key", max_retries=0))


@pytest.mark.asyncio
@pytest.mark.parametrize("error_name", ["ReadTimeoutError", "ConnectTimeoutError"])
async def test_bedrock_sdk_deadlines_are_timeouts(monkeypatch, error_name):
    boto3 = pytest.importorskip("boto3")
    import botocore.exceptions

    from ifixai.providers.bedrock import BedrockProvider

    class OwnedClient:
        def converse(self, **kwargs):
            raise getattr(botocore.exceptions, error_name)(endpoint_url="http://owned.invalid")

    class OwnedSession:
        def client(self, **kwargs):
            return OwnedClient()

    monkeypatch.setattr(boto3, "Session", lambda **kwargs: OwnedSession())
    with pytest.raises(ProviderTimeoutError):
        await BedrockProvider().send_message([ChatMessage(content="hello")], ProviderConfig(provider="bedrock", model="owned-model", max_retries=0))
