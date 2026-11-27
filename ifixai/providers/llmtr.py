import asyncio

import openai

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    create_chat_completion_json_fallback,
    openai_http_client,
    raise_for_http_status,
    raise_if_choice_errored,
    raise_if_truncated,
    sdk_error_text,
)
from ifixai.providers.client_pool import close_cached_clients

DEFAULT_MODEL = "qwen/qwen3.8-27b-free"
DEFAULT_BASE_URL = "https://llmtr.com/v1"
# Hard ceiling on max_tokens for LLMTR calls. Per-call ``config.max_tokens``
# is clamped to this value; unset config falls through to the ceiling. Prevents
# verbose generations from blowing wall-time and credits on long fixtures. Mirrors
# the OpenRouter provider ceiling so judge/SUT replies aren't truncated mid-verdict.
MAX_TOKENS_CEILING: int = 8192

ClientCacheKey = tuple[str, str | None, float, int]


class LLMTRProvider(ChatProvider):
    def __init__(self) -> None:
        self._clients: dict[ClientCacheKey, openai.AsyncOpenAI] = {}
        self._client_lock = asyncio.Lock()

    async def get_client(self, config: ProviderConfig) -> openai.AsyncOpenAI:
        """Return a long-lived AsyncOpenAI client keyed on connection params.

        Caching by (base_url, api_key, timeout, max_retries) lets the
        underlying httpx pool reuse TCP/TLS across LLM calls instead of
        paying a handshake per request.
        """
        base_url = config.endpoint or DEFAULT_BASE_URL
        key: ClientCacheKey = (
            base_url,
            config.api_key,
            float(config.timeout),
            config.max_retries,
        )
        cached = self._clients.get(key)
        if cached is not None:
            return cached
        async with self._client_lock:
            cached = self._clients.get(key)
            if cached is not None:
                return cached
            client = openai.AsyncOpenAI(
                api_key=config.api_key,
                base_url=base_url,
                timeout=float(config.timeout),
                max_retries=config.max_retries,
                http_client=openai_http_client(openai),
            )
            self._clients[key] = client
            return client

    async def aclose(self) -> None:
        await close_cached_clients(self._clients)

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        base_url = config.endpoint or DEFAULT_BASE_URL
        model = config.model or DEFAULT_MODEL
        formatted = [{"role": m.role, "content": m.content} for m in messages]

        client = await self.get_client(config)
        try:
            effective_max_tokens = (
                min(config.max_tokens, MAX_TOKENS_CEILING)
                if config.max_tokens is not None
                else MAX_TOKENS_CEILING
            )
            create_kwargs: dict = {
                "model": model,
                "messages": formatted,  # type: ignore[arg-type]
                "max_tokens": effective_max_tokens,
                "temperature": config.temperature,
            }
            if config.seed is not None:
                create_kwargs["seed"] = config.seed
            if config.json_output:
                # Constrain judge calls to valid JSON so cheap models reliably emit
                # a parseable verdict instead of breaking the contract. Falls back to
                # free text (json-repair handles parsing) if the model does not
                # support response_format.
                #
                # No `reasoning` suppression here, unlike OpenRouter. `reasoning` is
                # OpenRouter's own gateway extension, not an OpenAI-SDK parameter,
                # and LLMTR documents no equivalent. Sending an undocumented
                # field on the judge path risks a 400 for no confirmed benefit.
                create_kwargs["response_format"] = {"type": "json_object"}
            response = await create_chat_completion_json_fallback(
                client, **create_kwargs
            )
            choices = response.choices
            if not choices:
                raise ProviderResponseError(
                    provider="llmtr",
                    endpoint=base_url,
                    details=f"No choices in response (id={response.id})",
                )
            choice = choices[0]
            finish_reason = choice.finish_reason or "unknown"
            if choice.message is None:
                raise ProviderResponseError(
                    provider="llmtr",
                    endpoint=base_url,
                    details=f"Missing message in choice (finish_reason={finish_reason})",
                )
            content = choice.message.content
            raise_if_choice_errored("llmtr", base_url, choice, content or "")
            if config.reject_truncated:
                raise_if_truncated("llmtr", base_url, finish_reason, content or "")
            if not content:
                raise ProviderEmptyContentError(
                    provider="llmtr",
                    endpoint=base_url,
                    details=f"Empty content in response (finish_reason={finish_reason})",
                )
        except openai.AuthenticationError as exc:
            raise ProviderAuthError(
                provider="llmtr", endpoint=base_url, details=sdk_error_text(exc)
            ) from exc
        except openai.RateLimitError as exc:
            raise ProviderRateLimitError(
                provider="llmtr", endpoint=base_url, details=sdk_error_text(exc)
            ) from exc
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError(
                provider="llmtr", endpoint=base_url, details=sdk_error_text(exc)
            ) from exc
        except openai.APIConnectionError as exc:
            raise ProviderConnectionError(
                provider="llmtr", endpoint=base_url, details=sdk_error_text(exc)
            ) from exc
        except openai.APIStatusError as exc:
            # LLMTR is an OpenAI-compatible gateway. Retryable statuses (408,
            # 500, 502, 503, 504) are treated as transient overload; everything
            # else stays a response error.
            raise_for_http_status("llmtr", base_url, exc)
        except openai.APIError as exc:
            raise ProviderResponseError(
                provider="llmtr", endpoint=base_url, details=sdk_error_text(exc)
            ) from exc
        else:
            return content
