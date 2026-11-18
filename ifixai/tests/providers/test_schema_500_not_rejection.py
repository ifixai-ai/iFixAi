"""A generic HTTP 500 is not a refusal of the strict judge schema."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openai
import pytest

from ifixai.providers.base import (
    ProviderSchemaRejectedError,
    create_chat_completion_json_fallback,
)

_SCHEMA = {"type": "json_schema", "json_schema": {"name": "verdict"}}


class _HTTPResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.request = None


def server_error(message: str) -> openai.InternalServerError:
    return openai.InternalServerError(
        message,
        response=_HTTPResponse(500),  # type: ignore[arg-type]
        body={"error": {"message": message}},
    )


def _client(create: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.mark.parametrize("message", ["Internal Server Error", "upstream provider unavailable"])
async def test_transient_500_is_not_a_schema_rejection(message: str) -> None:
    error = server_error(message)
    create = AsyncMock(side_effect=error)
    with pytest.raises(openai.InternalServerError) as caught:
        await create_chat_completion_json_fallback(_client(create), response_format=_SCHEMA)
    assert caught.value is error
    assert create.await_count == 1


async def test_500_naming_the_schema_is_still_a_rejection() -> None:
    create = AsyncMock(side_effect=server_error("json_schema response_format is not supported"))
    with pytest.raises(ProviderSchemaRejectedError):
        await create_chat_completion_json_fallback(_client(create), response_format=_SCHEMA)
