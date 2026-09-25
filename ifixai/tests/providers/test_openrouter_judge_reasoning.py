"""A judge request never sends `reasoning.effort` and `reasoning.max_tokens` together.

OpenRouter 400s on that pair and the fallback retries, doubling every judge call.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import openai
import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.openrouter import REASONING_DISABLED, OpenRouterProvider

_BOTH = 'Only one of "reasoning.effort" and "reasoning.max_tokens" can be specified'
_NO_JSON_MODE = "response_format is not supported by this model"


class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None, finish_reason: str) -> None:
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Response:
    def __init__(self, content: str | None, finish_reason: str = "stop") -> None:
        self.choices = [_Choice(content, finish_reason)]
        self.id = "gen-test"


class _HTTPResponse:
    """Minimal response for building an SDK status error."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.request = None


def _bad_request(message: str) -> openai.BadRequestError:
    return openai.BadRequestError(
        message,
        response=_HTTPResponse(400),  # type: ignore[arg-type]
        body={"message": message},
    )


class _Completions:
    """Fake SDK completions that 400 like OpenRouter and record every body."""

    def __init__(
        self, responses: list[_Response], *, no_json_mode: bool = False
    ) -> None:
        self.responses = responses
        self.no_json_mode = no_json_mode
        self.calls: list[dict] = []
        self.reasoning_sent: list[dict] = []

    async def create(self, **kwargs: Any) -> _Response:
        self.calls.append(dict(kwargs))
        reasoning = (kwargs.get("extra_body") or {}).get("reasoning") or {}
        self.reasoning_sent.append(dict(reasoning))
        if "effort" in reasoning and "max_tokens" in reasoning:
            raise _bad_request(_BOTH)
        if self.no_json_mode and "response_format" in kwargs:
            raise _bad_request(_NO_JSON_MODE)
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def _provider(
    responses: list[_Response], *, no_json_mode: bool = False
) -> tuple[OpenRouterProvider, _Completions]:
    provider = OpenRouterProvider()
    completions = _Completions(responses, no_json_mode=no_json_mode)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider.get_client = AsyncMock(return_value=client)  # type: ignore[method-assign]
    return provider, completions


def _judge_config() -> ProviderConfig:
    return ProviderConfig(
        provider="openrouter",
        api_key="k",
        model="deepseek/deepseek-chat",
        json_output=True,
        max_retries=0,
    )


def _reasoning(call: dict) -> dict:
    return dict((call.get("extra_body") or {}).get("reasoning") or {})


@pytest.mark.unit
def test_the_switch_itself_names_neither_effort_nor_a_token_budget() -> None:
    assert "effort" not in REASONING_DISABLED
    assert "max_tokens" not in REASONING_DISABLED
    assert REASONING_DISABLED == {"enabled": False, "exclude": True}


@pytest.mark.unit
async def test_a_judge_verdict_costs_one_round_trip() -> None:
    provider, completions = _provider([_Response('{"verdict": "pass"}')])
    reply = await provider.send_message(
        [ChatMessage(role="user", content="grade")], _judge_config()
    )
    assert reply == '{"verdict": "pass"}'
    assert len(completions.calls) == 1
    reasoning = _reasoning(completions.calls[0])
    assert reasoning == {"enabled": False, "exclude": True}
    assert not {"effort", "max_tokens"} <= reasoning.keys()


@pytest.mark.unit
async def test_no_judge_body_carries_both_keys_even_on_the_fallback_retry() -> None:
    """A model without JSON mode forces the fallback retry."""
    provider, completions = _provider([_Response('{"ok": 1}')], no_json_mode=True)
    reply = await provider.send_message(
        [ChatMessage(role="user", content="grade")], _judge_config()
    )
    assert reply == '{"ok": 1}'
    assert len(completions.reasoning_sent) == 2
    for reasoning in completions.reasoning_sent:
        assert not ("effort" in reasoning and "max_tokens" in reasoning), reasoning
    assert completions.reasoning_sent[-1] == {"enabled": False, "exclude": True}


@pytest.mark.unit
async def test_the_switch_is_never_shared_between_requests() -> None:
    provider, completions = _provider([_Response("{}")])
    await provider.send_message(
        [ChatMessage(role="user", content="a")], _judge_config()
    )
    completions.calls[0]["extra_body"]["reasoning"]["enabled"] = True
    assert REASONING_DISABLED["enabled"] is False
