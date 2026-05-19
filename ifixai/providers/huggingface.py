import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from huggingface_hub import InferenceClient

from ifixai.providers.base import (
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from ifixai.core.types import ChatMessage, ProviderConfig

try:
    from huggingface_hub import InferenceClient
    from huggingface_hub.utils import HfHubHTTPError

    HAS_HUGGINGFACE = True
except ImportError:
    HAS_HUGGINGFACE = False

INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0


class HuggingFaceProvider(ChatProvider):

    def __init__(self) -> None:
        if not HAS_HUGGINGFACE:
            raise ImportError(
                "The huggingface_hub package is required for the Hugging Face provider. "
                "Install it with: pip install ifixai[huggingface]"
            )

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        endpoint = config.endpoint or "https://api-inference.huggingface.co"

        client = InferenceClient(
            model=config.model or None,
            token=config.api_key or None,
            timeout=float(config.timeout),
        )

        formatted_messages = _format_messages(messages)

        attempts = config.max_retries + 1
        backoff = INITIAL_BACKOFF_SECONDS

        for attempt in range(attempts):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        _call_chat_completion,
                        client,
                        formatted_messages,
                        config.model,
                        config.temperature,
                        config.seed,
                        config.max_tokens,
                    ),
                    timeout=float(config.timeout),
                )
                return response

            except asyncio.TimeoutError as exc:
                raise ProviderTimeoutError(
                    provider="huggingface",
                    endpoint=endpoint,
                    details=f"Request timed out after {config.timeout}s",
                ) from exc
            except HfHubHTTPError as exc:
                status_code = _extract_status_code(exc)

                if status_code in (401, 403):
                    raise ProviderAuthError(
                        provider="huggingface",
                        endpoint=endpoint,
                        details=str(exc),
                    ) from exc

                if status_code == 429:
                    if attempt < attempts - 1:
                        await asyncio.sleep(backoff)
                        backoff *= BACKOFF_MULTIPLIER
                        continue
                    raise ProviderRateLimitError(
                        provider="huggingface",
                        endpoint=endpoint,
                        details=str(exc),
                    ) from exc

                if status_code == 503:
                    raise ProviderConnectionError(
                        provider="huggingface",
                        endpoint=endpoint,
                        details=str(exc),
                    ) from exc

                raise ProviderResponseError(
                    provider="huggingface",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except ConnectionError as exc:
                raise ProviderConnectionError(
                    provider="huggingface",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc

        raise ProviderRateLimitError(
            provider="huggingface",
            endpoint=endpoint,
            details="Exhausted all retry attempts",
        )


def _call_chat_completion(
    client: "InferenceClient",
    messages: list[dict[str, str]],
    model: str | None,
    temperature: float,
    seed: int | None,
    max_tokens: int | None,
) -> str:
    kwargs: dict = {
        "messages": messages,
        "model": model or None,
        "temperature": temperature,
    }
    if seed is not None:
        kwargs["seed"] = seed
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    response = client.chat_completion(**kwargs)  # type: ignore[arg-type]

    choices = response.choices  # type: ignore[union-attr]
    if not choices:
        raise ProviderResponseError(
            provider="huggingface",
            endpoint="",
            details=f"No choices in response (id={response.id})",  # type: ignore[union-attr]
        )

    choice = choices[0]
    finish_reason = choice.finish_reason or "unknown"
    if choice.message is None:
        raise ProviderResponseError(
            provider="huggingface",
            endpoint="",
            details=f"Missing message in choice (finish_reason={finish_reason})",
        )
    content = choice.message.content
    if not content:
        raise ProviderEmptyContentError(
            provider="huggingface",
            endpoint="",
            details=f"Empty content in response (finish_reason={finish_reason})",
        )

    return content


def _format_messages(
    messages: list[ChatMessage],
) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in messages]


def _extract_status_code(exc: Exception) -> int | None:
    try:
        response = exc.response
    except AttributeError:
        return None
    try:
        code = response.status_code
    except AttributeError:
        return None
    return code if isinstance(code, int) else None
