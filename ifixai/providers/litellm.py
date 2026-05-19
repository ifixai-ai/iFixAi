"""LiteLLM provider for iFixAi.

Routes to 100+ LLM providers (OpenAI, Anthropic, Google, Azure, Bedrock,
Ollama, etc.) via the litellm SDK. No proxy server needed.

Install: pip install ifixai[litellm]

See https://docs.litellm.ai/docs/providers for all supported models.
"""

import litellm as _litellm

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ChatProvider,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderResponseError,
    ProviderTimeoutError,
)

DEFAULT_MODEL = "openai/gpt-4o"


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
            "timeout": float(config.timeout),
            "temperature": config.temperature,
        }
        if config.seed is not None:
            params["seed"] = config.seed
        if config.max_tokens is not None:
            params["max_tokens"] = config.max_tokens
        if config.api_key:
            params["api_key"] = config.api_key
        if config.endpoint:
            params["api_base"] = config.endpoint

        try:
            response = await _litellm.acompletion(**params)

            choices = response.choices
            if not choices:
                raise ProviderResponseError(
                    provider="litellm",
                    endpoint=config.endpoint or "default",
                    details=f"No choices in response (id={response.id})",
                )
            choice = choices[0]
            finish_reason = choice.finish_reason or "unknown"
            if choice.message is None:
                raise ProviderResponseError(
                    provider="litellm",
                    endpoint=config.endpoint or "default",
                    details=f"Missing message in choice (finish_reason={finish_reason})",
                )
            content = choice.message.content
            if not content:
                raise ProviderEmptyContentError(
                    provider="litellm",
                    endpoint=config.endpoint or "default",
                    details=f"Empty content in response (finish_reason={finish_reason})",
                )
            return content

        except ProviderResponseError:
            raise
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                provider="litellm",
                endpoint=config.endpoint or "default",
                details=str(exc),
            ) from exc
        except ConnectionError as exc:
            raise ProviderConnectionError(
                provider="litellm",
                endpoint=config.endpoint or "default",
                details=str(exc),
            ) from exc
        except Exception as exc:
            raise ProviderResponseError(
                provider="litellm",
                endpoint=config.endpoint or "default",
                details=str(exc),
            ) from exc
