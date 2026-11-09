"""Rejected model parameters are adapted without losing the token budget."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openai
import pytest

from ifixai.providers.base import create_chat_completion_json_fallback


class _HTTPResponse:
    """Minimal response for building an SDK status error."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.request = None


def bad_request(message: str) -> openai.BadRequestError:
    return openai.BadRequestError(
        message,
        response=_HTTPResponse(400),  # type: ignore[arg-type]
        body={"error": {"message": message}},
    )


@pytest.mark.parametrize("reverse", [False, True])
async def test_adapts_sequential_rejections(reverse: bool) -> None:
    errors = [
        bad_request("Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."),
        bad_request("Unsupported value: 'temperature' does not support 0.0. Only 1 is supported."),
    ]
    if reverse:
        errors.reverse()
    create = AsyncMock(side_effect=[*errors, "ok"])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert await create_chat_completion_json_fallback(
        client, temperature_scope=(f"https://order-{reverse}.example", "gpt-5", "agent"),
        model="gpt-5", max_tokens=512, temperature=0.0,
        response_format={"type": "json_schema", "json_schema": {"name": "test"}},
    ) == "ok"
    final = create.call_args.kwargs
    assert final["max_completion_tokens"] == 512
    assert "max_tokens" not in final
    assert "temperature" not in final
    assert final["response_format"]["type"] == "json_schema"
    assert create.await_count == 3


@pytest.mark.parametrize("message", [
    "Unknown model", "temperature must be positive", "max_tokens exceeds context length",
])
async def test_unrelated_errors_are_not_retried(message: str) -> None:
    error = bad_request(message)
    create = AsyncMock(side_effect=error)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(openai.BadRequestError) as caught:
        await create_chat_completion_json_fallback(client, max_tokens=512, temperature=0.0)
    assert caught.value is error
    assert create.await_count == 1


@pytest.mark.parametrize("provider_name", ["openai", "azure"])
async def test_provider_remembers_temperature_rejection(provider_name, monkeypatch, caplog):
    from ifixai.core.types import ChatMessage, ProviderConfig
    from ifixai.providers.azure import AzureOpenAIProvider
    from ifixai.providers.openai import OpenAIProvider

    provider_class = OpenAIProvider if provider_name == "openai" else AzureOpenAIProvider
    provider = provider_class()
    config = ProviderConfig(provider=provider_name, model="temperature-test", endpoint=f"https://{provider_name}.example/v1")
    response = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="ok"), finish_reason="stop",
    )])
    create = AsyncMock(side_effect=[bad_request(
        "Unsupported value: 'temperature' does not support 0.0. Only 1 is supported."
    ), response, response])
    client = SimpleNamespace(base_url=config.endpoint, chat=SimpleNamespace(
        completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(provider, "get_client", AsyncMock(return_value=client))
    assert provider.accepts_temperature(config.model, config.endpoint)
    messages = [ChatMessage(role="user", content="hello")]
    assert await provider.send_message(messages, config) == "ok"
    assert not provider.accepts_temperature(config.model, config.endpoint)
    assert not provider_class().accepts_temperature(config.model, config.endpoint)
    assert provider.accepts_temperature("another-model", config.endpoint)
    assert provider.accepts_temperature(config.model, "https://other.example/v1")
    assert await provider.send_message(messages, config) == "ok"
    assert create.await_count == 3
    assert "temperature" not in create.call_args_list[2].kwargs
    assert sum("default temperature" in record.message for record in caplog.records) == 1


async def test_unscoped_helper_does_not_drop_temperature():
    error = bad_request("Unsupported parameter: temperature")
    create = AsyncMock(side_effect=error)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(openai.BadRequestError):
        await create_chat_completion_json_fallback(client, temperature=0.0)
    assert create.await_count == 1
