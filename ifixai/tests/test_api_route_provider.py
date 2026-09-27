import json

import httpx
import openai
import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.api_route import ApiRouteProvider
from ifixai.providers.base import ProviderAuthError
from ifixai.providers.resolver import credential_env_vars, resolve_provider
from ifixai.reporting.scorecard import grading_vendor


@pytest.mark.asyncio
async def test_api_route_uses_openai_chat_completions(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-5.5",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Hello"},
                    }
                ],
            },
        )

    real_client = openai.AsyncOpenAI
    transport = httpx.MockTransport(respond)

    def mock_client(**kwargs: object) -> openai.AsyncOpenAI:
        return real_client(**kwargs, http_client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr("ifixai.providers.api_route.openai.AsyncOpenAI", mock_client)
    provider = resolve_provider("api_route")
    assert isinstance(provider, ApiRouteProvider)
    config = ProviderConfig(provider="api_route", api_key="test-key")
    try:
        result = await provider.send_message([ChatMessage(content="Hi")], config)
    finally:
        await provider.aclose()

    assert result == "Hello"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://global.api-route.com/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-key"
    body = json.loads(requests[0].content)
    assert body["model"] == "gpt-5.5"
    assert body["messages"] == [{"role": "user", "content": "Hi"}]
    assert body["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_api_route_maps_authentication_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = openai.AsyncOpenAI
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": {"message": "invalid key"}})
    )

    def mock_client(**kwargs: object) -> openai.AsyncOpenAI:
        return real_client(**kwargs, http_client=httpx.AsyncClient(transport=transport))

    monkeypatch.setattr("ifixai.providers.api_route.openai.AsyncOpenAI", mock_client)
    provider = ApiRouteProvider()
    config = ProviderConfig(provider="api_route", api_key="invalid", max_retries=0)
    try:
        with pytest.raises(ProviderAuthError):
            await provider.send_message([ChatMessage(content="Hi")], config)
    finally:
        await provider.aclose()


def test_api_route_credential_and_grading_vendor() -> None:
    assert credential_env_vars("api_route") == ("API_ROUTE_API_KEY",)
    assert grading_vendor("api_route", "gpt-5.5") == "openai"
    assert grading_vendor("api_route", "claude-sonnet-4") == "anthropic"
    assert grading_vendor("api_route", "google/gemini-2.5-pro") == "google"
