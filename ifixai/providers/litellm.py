"""LiteLLM provider for iFixAi.

Routes to 100+ LLM providers (OpenAI, Anthropic, Google, Azure, Bedrock,
Ollama, etc.) via the litellm SDK. No proxy server needed.

Install: pip install ifixai[litellm]

See https://docs.litellm.ai/docs/providers for all supported models.
"""

from types import SimpleNamespace
from typing import Any

import litellm as _litellm
import openai

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderError,
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    raise_for_http_status,
    raise_if_choice_errored,
    raise_if_truncated,
)
from ifixai.providers.http import content_flag_refusal

DEFAULT_MODEL = "openai/gpt-4o"


def _auth_refusal(endpoint: str, exc: Exception) -> ProviderError:
    """A 401/403 from the SDK. A gateway's content-flag 403 fails only this request;
    any other is a key refusal."""
    flagged = content_flag_refusal("litellm", endpoint, getattr(exc, "status_code", 0), str(exc))
    if flagged is not None:
        return flagged
    return ProviderAuthError(provider="litellm", endpoint=endpoint, details=str(exc))


class LiteLLMProvider(ChatProvider):
    def __init__(self) -> None:
        pass

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        model = config.model or DEFAULT_MODEL
        formatted_messages = [{"role": m.role, "content": m.content} for m in messages]

        params = {
            "model": model,
            "messages": formatted_messages,
            "drop_params": True,
            "max_retries": config.max_retries,
            "timeout": float(config.timeout),
            "temperature": config.temperature,
        }
        if config.seed is not None:
            params["seed"] = config.seed
        if config.max_tokens is not None:
            params["max_tokens"] = config.max_tokens
        if config.json_output:
            # Judge calls only: ask for valid JSON so cheap models emit a parseable
            # verdict. drop_params=True silently drops it on backends that lack it.
            params["response_format"] = {"type": "json_object"}
        if config.api_key:
            params["api_key"] = config.api_key
        if config.endpoint:
            params["api_base"] = config.endpoint

        try:
            response = await _litellm.acompletion(**params)

            choices = response.choices
            if not choices:
                raise ProviderResponseError(  # noqa: TRY301 — inner functions are disallowed by project style
                    provider="litellm",
                    endpoint=config.endpoint or "default",
                    details=f"No choices in response (id={response.id})",
                )
            choice = choices[0]
            finish_reason = choice.finish_reason or "unknown"
            if choice.message is None:
                raise ProviderResponseError(  # noqa: TRY301 — inner functions are disallowed by project style
                    provider="litellm",
                    endpoint=config.endpoint or "default",
                    details=f"Missing message in choice (finish_reason={finish_reason})",
                )
            content = choice.message.content
            raise_if_choice_errored(
                "litellm", config.endpoint or "default", _native_choice(choice), content or ""
            )
            if config.reject_truncated:
                raise_if_truncated(
                    "litellm", config.endpoint or "default", finish_reason, content or ""
                )
            if not content:
                raise ProviderEmptyContentError(  # noqa: TRY301 — inner functions are disallowed by project style
                    provider="litellm",
                    endpoint=config.endpoint or "default",
                    details=f"Empty content in response (finish_reason={finish_reason})",
                )

        except ProviderError:
            raise
        except (_litellm.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise _auth_refusal(config.endpoint or "default", exc) from exc
        except _litellm.RateLimitError as exc:
            raise ProviderRateLimitError(
                provider="litellm",
                endpoint=config.endpoint or "default",
                details=str(exc),
            ) from exc
        except (_litellm.InternalServerError, _litellm.ServiceUnavailableError) as exc:
            raise ProviderOverloadedError(
                provider="litellm",
                endpoint=config.endpoint or "default",
                details=str(exc),
            ) from exc
        except (_litellm.Timeout, TimeoutError) as exc:
            raise ProviderTimeoutError(
                provider="litellm",
                endpoint=config.endpoint or "default",
                details=str(exc),
            ) from exc
        except (_litellm.APIConnectionError, ConnectionError) as exc:
            raise ProviderConnectionError(
                provider="litellm",
                endpoint=config.endpoint or "default",
                details=str(exc),
            ) from exc
        except (_litellm.APIError, openai.APIStatusError) as exc:
            status = getattr(exc, "status_code", None)
            if status in (401, 403):
                raise _auth_refusal(config.endpoint or "default", exc) from exc
            if status == 429:
                raise ProviderRateLimitError(
                    provider="litellm",
                    endpoint=config.endpoint or "default",
                    details=str(exc),
                ) from exc
            raise_for_http_status("litellm", config.endpoint or "default", exc)
        except Exception as exc:
            raise ProviderResponseError(
                provider="litellm",
                endpoint=config.endpoint or "default",
                details=str(exc),
            ) from exc
        else:
            return content


def _native_choice(choice: Any) -> Any:
    """The choice as the upstream sent it.

    LiteLLM maps an unrecognised ``finish_reason="error"`` to ``"stop"`` and moves
    the upstream's ``error`` object into ``provider_specific_fields``, which would
    hide a mid-generation abort from ``raise_if_choice_errored``.
    """
    fields = getattr(choice, "provider_specific_fields", None) or {}
    native = fields.get("native_finish_reason")
    if not native:
        return choice
    return SimpleNamespace(finish_reason=native, error=fields.get("error"))
