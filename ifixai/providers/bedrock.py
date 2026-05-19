import asyncio

import boto3
import botocore.exceptions

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
from ifixai.providers.schemas import ConversePayload

INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0


class BedrockProvider(ChatProvider):

    def __init__(
        self,
        region_name: str = "us-east-1",
        profile_name: str | None = None,
    ) -> None:
        self.region_name = region_name
        self.profile_name = profile_name

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        if not config.model:
            raise ProviderResponseError(
                provider="bedrock",
                endpoint=f"bedrock-runtime.{self.region_name}.amazonaws.com",
                details=(
                    "Bedrock model ID is required. "
                    "Set config.model to a Bedrock model ID "
                    "(e.g., 'anthropic.claude-3-sonnet-20240229-v1:0')."
                ),
            )

        endpoint = (
            config.endpoint or f"bedrock-runtime.{self.region_name}.amazonaws.com"
        )

        session_kwargs: dict = {"region_name": self.region_name}
        if self.profile_name:
            session_kwargs["profile_name"] = self.profile_name

        session = boto3.Session(**session_kwargs)

        client_kwargs: dict = {"service_name": "bedrock-runtime"}
        if config.endpoint:
            client_kwargs["endpoint_url"] = config.endpoint

        bedrock_client = session.client(**client_kwargs)

        converse_payload = _format_for_converse(messages)
        system_prompts = converse_payload["system_prompts"]
        converse_messages = converse_payload["messages"]

        attempts = config.max_retries + 1
        backoff = INITIAL_BACKOFF_SECONDS

        inference_config: dict = {"temperature": config.temperature}
        if config.max_tokens is not None:
            inference_config["maxTokens"] = config.max_tokens
        # Bedrock converse does not expose `seed` in inferenceConfig; seed
        # is recorded on ProviderConfig for manifest reproducibility only.

        for attempt in range(attempts):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        _invoke_converse,
                        bedrock_client,
                        config.model,
                        system_prompts,
                        converse_messages,
                        inference_config,
                    ),
                    timeout=float(config.timeout),
                )
                return response

            except asyncio.TimeoutError as exc:
                raise ProviderTimeoutError(
                    provider="bedrock",
                    endpoint=endpoint,
                    details=f"Request timed out after {config.timeout}s",
                ) from exc
            except botocore.exceptions.NoCredentialsError as exc:
                raise ProviderAuthError(
                    provider="bedrock",
                    endpoint=endpoint,
                    details=f"AWS credentials not found: {exc}",
                ) from exc
            except botocore.exceptions.ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code", "")

                if error_code in (
                    "AccessDeniedException",
                    "UnrecognizedClientException",
                ):
                    raise ProviderAuthError(
                        provider="bedrock",
                        endpoint=endpoint,
                        details=str(exc),
                    ) from exc

                if error_code == "ThrottlingException":
                    if attempt < attempts - 1:
                        await asyncio.sleep(backoff)
                        backoff *= BACKOFF_MULTIPLIER
                        continue
                    raise ProviderRateLimitError(
                        provider="bedrock",
                        endpoint=endpoint,
                        details=str(exc),
                    ) from exc

                if error_code in (
                    "ServiceUnavailableException",
                    "InternalServerException",
                ):
                    raise ProviderConnectionError(
                        provider="bedrock",
                        endpoint=endpoint,
                        details=str(exc),
                    ) from exc

                raise ProviderResponseError(
                    provider="bedrock",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            except botocore.exceptions.EndpointConnectionError as exc:
                raise ProviderConnectionError(
                    provider="bedrock",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc

        raise ProviderRateLimitError(
            provider="bedrock",
            endpoint=endpoint,
            details="Exhausted all retry attempts",
        )


def _invoke_converse(
    client: object,
    model_id: str,
    system_prompts: list[dict],
    messages: list[dict],
    inference_config: dict,
) -> str:
    converse_kwargs: dict = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": inference_config,
    }
    if system_prompts:
        converse_kwargs["system"] = system_prompts

    response = client.converse(**converse_kwargs)  # type: ignore[union-attr]

    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])

    if not content_blocks:
        raise ProviderEmptyContentError(
            provider="bedrock",
            endpoint="",
            details="Empty content in Bedrock converse response",
        )

    text_parts = [block["text"] for block in content_blocks if "text" in block]
    if not text_parts:
        raise ProviderResponseError(
            provider="bedrock",
            endpoint="",
            details="No text blocks in Bedrock converse response",
        )

    return "\n".join(text_parts)


def _format_for_converse(
    messages: list[ChatMessage],
) -> ConversePayload:
    system_prompts: list[dict] = []
    converse_messages: list[dict] = []

    for msg in messages:
        if msg.role == "system":
            system_prompts.append({"text": msg.content})
        else:
            converse_messages.append(
                {
                    "role": msg.role,
                    "content": [{"text": msg.content}],
                }
            )

    return ConversePayload(system_prompts=system_prompts, messages=converse_messages)
