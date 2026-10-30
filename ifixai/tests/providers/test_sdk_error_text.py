"""The provider's whole reply body reaches the error matchers on every OpenAI SDK.

openai 3.38 put only ``body["error"]["message"]`` in the exception text, so the
quota code, error type and gateway fields the matchers read went missing. These
build both SDKs' error text by hand, so they hold whichever SDK is installed.
"""

from __future__ import annotations

from typing import Any

import pytest

from ifixai.providers.base import (
    ProviderRateLimitError,
    is_fatal_provider_error,
    sdk_error_text,
)

SPENT_QUOTA = {"error": {"message": "You exceeded your current quota, please check your plan.",
                         "type": "insufficient_quota", "code": "insufficient_quota"}}
GATEWAY_REFUSAL = {"success": False, "result": [], "messages": [],
                   "errors": [{"code": 10000, "message": "Unauthorized"}]}


class SdkError(Exception):
    def __init__(self, text: str, body: Any) -> None:
        super().__init__(text)
        self.body = body


def old_sdk(status: int, body: Any) -> SdkError:
    return SdkError(f"Error code: {status} - {body}", body)


def new_sdk(status: int, body: Any) -> SdkError:
    error = body.get("error", body) if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return SdkError(f"Error code: {status}" + (f" - {message}" if message else ""), body)


def test_the_old_sdk_text_is_kept_as_it_was() -> None:
    exc = old_sdk(429, SPENT_QUOTA)
    assert sdk_error_text(exc) == str(exc)


@pytest.mark.parametrize("body", [SPENT_QUOTA, GATEWAY_REFUSAL])
def test_the_new_sdk_text_gets_the_body_back(body: dict) -> None:
    assert str(body) in sdk_error_text(new_sdk(401, body))


def test_a_spent_quota_stops_the_run_on_the_new_sdk() -> None:
    exc = ProviderRateLimitError(provider="openai", endpoint="x", details=sdk_error_text(new_sdk(429, SPENT_QUOTA)))
    assert is_fatal_provider_error(exc)


@pytest.mark.parametrize("body", [None, ""])
def test_an_error_with_no_body_keeps_its_text(body: Any) -> None:
    assert sdk_error_text(SdkError("Connection error.", body)) == "Connection error."
