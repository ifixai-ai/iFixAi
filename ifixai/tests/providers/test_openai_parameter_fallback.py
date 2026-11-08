"""Rejected model parameters are adapted without losing the token budget."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from ifixai.providers.base import create_chat_completion_json_fallback


def bad_request(message: str) -> openai.BadRequestError:
    return openai.BadRequestError(
        message,
        response=httpx.Response(400, request=httpx.Request("POST", "https://example.com")),
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
        client, model="gpt-5", max_tokens=512, temperature=0.0,
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
