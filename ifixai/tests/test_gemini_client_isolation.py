"""Concurrent Gemini callers must retain their own request credentials."""

import asyncio

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderConnectionError


@pytest.mark.asyncio
@pytest.mark.parametrize("keys", [("owned-key-A", "owned-key-B"), ("owned-key-A", "owned-key-A")])
async def test_concurrent_gemini_requests_keep_their_own_api_key(monkeypatch, keys):
    genai = pytest.importorskip("google.generativeai")
    glm = pytest.importorskip("google.ai.generativelanguage")
    from google.generativeai import client

    from ifixai.providers import gemini

    native_clients = []

    def request_client(**kwargs):
        # Construct the actual installed async SDK client, including API-key
        # credentials. Substitute only the final outbound generation call.
        native = glm.GenerativeServiceAsyncClient(**kwargs)
        native_clients.append(native)

        async def generate_content(request, **options):
            await asyncio.sleep(0)
            api_key = native.transport._credentials.token
            return glm.GenerateContentResponse(candidates=[glm.Candidate(
                content=glm.Content(parts=[glm.Part(text=api_key)]),
                finish_reason=glm.Candidate.FinishReason.STOP,
            )])

        monkeypatch.setattr(native, "generate_content", generate_content)
        return native

    def lazy_global_client():
        return request_client(client_options=client._client_manager.client_config["client_options"])

    monkeypatch.setattr(client, "get_default_generative_async_client", lazy_global_client)
    monkeypatch.setattr(gemini, "GenerativeServiceAsyncClient", request_client, raising=False)
    provider = gemini.GeminiProvider()
    try:
        results = await asyncio.gather(*[
            provider.send_message([ChatMessage(content="hello")], ProviderConfig(provider="gemini", api_key=key, max_retries=0))
            for key in keys
        ])
        assert results == list(keys)
    finally:
        for native in native_clients:
            await native.transport.close()
    assert genai is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["success", "error", "cancel"])
async def test_request_owned_sdk_client_closes_on_every_exit(monkeypatch, exit_kind):
    pytest.importorskip("google.generativeai")
    glm = pytest.importorskip("google.ai.generativelanguage")
    grpc = pytest.importorskip("grpc")
    from google.api_core.exceptions import ServiceUnavailable
    from google.generativeai import client

    from ifixai.providers import gemini

    native_clients = []
    entered = asyncio.Event()

    def request_client(**kwargs):
        native = glm.GenerativeServiceAsyncClient(**kwargs)
        native_clients.append(native)

        async def generate_content(request, **options):
            if exit_kind == "error":
                raise ServiceUnavailable("owned transport unavailable")
            if exit_kind == "cancel":
                entered.set()
                await asyncio.Event().wait()
            return glm.GenerateContentResponse(candidates=[glm.Candidate(
                content=glm.Content(parts=[glm.Part(text="owned reply")]),
                finish_reason=glm.Candidate.FinishReason.STOP,
            )])

        monkeypatch.setattr(native, "generate_content", generate_content)
        return native

    monkeypatch.setattr(client, "get_default_generative_async_client", lambda: request_client(client_options=client._client_manager.client_config["client_options"]))
    monkeypatch.setattr(gemini, "GenerativeServiceAsyncClient", request_client, raising=False)
    config = ProviderConfig(provider="gemini", api_key="owned-key-A", max_retries=0)
    try:
        call = gemini.GeminiProvider().send_message([ChatMessage(content="hello")], config)
        if exit_kind == "cancel":
            task = asyncio.create_task(call)
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif exit_kind == "error":
            with pytest.raises(ProviderConnectionError):
                await call
        else:
            assert await call == "owned reply"
        assert len(native_clients) == 1
        assert native_clients[0].transport.grpc_channel.get_state() == grpc.ChannelConnectivity.SHUTDOWN
    finally:
        for native in native_clients:
            await native.transport.close()
