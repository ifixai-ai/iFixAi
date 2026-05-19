import asyncio

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

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
from ifixai.providers.schemas import GeminiMessages

DEFAULT_MODEL = "gemini-2.0-flash"

INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0


class GeminiProvider(ChatProvider):

    def __init__(self) -> None:
        pass

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        genai.configure(api_key=config.api_key)

        model_name = config.model or DEFAULT_MODEL
        endpoint = config.endpoint or "https://generativelanguage.googleapis.com"

        formatted = _format_messages(messages)
        system_instruction = formatted["system_instruction"]
        contents = formatted["contents"]

        gen_config_kwargs: dict = {
            "candidate_count": 1,
            "temperature": config.temperature,
        }
        if config.max_tokens is not None:
            gen_config_kwargs["max_output_tokens"] = config.max_tokens
        # Gemini SDK does not expose a `seed` parameter at the time of
        # writing. Seed remains tracked on ProviderConfig for manifest
        # reproducibility; the API call cannot pin it.
        generation_config = genai.types.GenerationConfig(**gen_config_kwargs)

        model_kwargs: dict = {"model_name": model_name}
        if system_instruction:
            model_kwargs["system_instruction"] = system_instruction

        model = genai.GenerativeModel(
            generation_config=generation_config,
            **model_kwargs,
        )

        attempts = config.max_retries + 1
        backoff = INITIAL_BACKOFF_SECONDS

        for attempt in range(attempts):
            try:
                response = await asyncio.wait_for(
                    model.generate_content_async(contents),
                    timeout=float(config.timeout),
                )

                if not response.candidates:
                    raise ProviderResponseError(
                        provider="gemini",
                        endpoint=endpoint,
                        details="No candidates in response",
                    )

                candidate = response.candidates[0]
                text_parts = [
                    part.text
                    for part in candidate.content.parts
                    if hasattr(part, "text") and part.text
                ]
                if not text_parts:
                    raise ProviderEmptyContentError(
                        provider="gemini",
                        endpoint=endpoint,
                        details="No text parts in response candidate",
                    )
                return "\n".join(text_parts)

            except asyncio.TimeoutError as exc:
                raise ProviderTimeoutError(
                    provider="gemini",
                    endpoint=endpoint,
                    details=f"Request timed out after {config.timeout}s",
                ) from exc
            except google_exceptions.Unauthenticated as exc:
                raise ProviderAuthError(
                    provider="gemini",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except google_exceptions.PermissionDenied as exc:
                raise ProviderAuthError(
                    provider="gemini",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except google_exceptions.ResourceExhausted as exc:
                if attempt < attempts - 1:
                    await asyncio.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER
                    continue
                raise ProviderRateLimitError(
                    provider="gemini",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except google_exceptions.ServiceUnavailable as exc:
                raise ProviderConnectionError(
                    provider="gemini",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except google_exceptions.GoogleAPIError as exc:
                raise ProviderResponseError(
                    provider="gemini",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc

        raise ProviderRateLimitError(
            provider="gemini",
            endpoint=endpoint,
            details="Exhausted all retry attempts",
        )


def _format_messages(
    messages: list[ChatMessage],
) -> GeminiMessages:
    system_instruction = ""
    contents: list[dict] = []

    for msg in messages:
        if msg.role == "system":
            system_instruction = msg.content
        else:
            gemini_role = "model" if msg.role == "assistant" else "user"
            contents.append(
                {
                    "role": gemini_role,
                    "parts": [{"text": msg.content}],
                }
            )

    return GeminiMessages(system_instruction=system_instruction, contents=contents)
