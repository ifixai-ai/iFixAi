import asyncio

import boto3
import botocore.exceptions
from botocore.config import Config

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import (
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderEmptyContentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    raise_if_truncated,
)
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

        # The adapter owns this client and its retry loop. Configure the native
        # socket limits too: cancelling to_thread cannot stop a blocking read.
        # Nonpositive values retain the native defaults: wait_for reports the
        # existing immediate timeout without starting a request.
        socket_limits = (
            {
                "connect_timeout": float(config.timeout),
                "read_timeout": float(config.timeout),
            }
            if config.timeout > 0 else {}
        )
        client_kwargs: dict = {
            "service_name": "bedrock-runtime",
            "config": Config(
                retries={"total_max_attempts": 1},
                **socket_limits,
            ),
        }
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
                        config.reject_truncated,
                    ),
                    timeout=float(config.timeout),
                )
            except (
                asyncio.TimeoutError,
                botocore.exceptions.ReadTimeoutError,
                botocore.exceptions.ConnectTimeoutError,
            ) as exc:
                raise ProviderTimeoutError(
                    provider="bedrock",
                    endpoint=endpoint,
                    details=(
                        f"Request timed out after {config.timeout}s"
                        if isinstance(exc, asyncio.TimeoutError) else str(exc)
                    ),
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

                http_status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if error_code in (
                    "ModelNotReadyException",
                    "ServiceUnavailableException",
                    "InternalServerException",
                ) or http_status in (500, 502, 503, 504):
                    if attempt < attempts - 1:
                        await asyncio.sleep(backoff)
                        backoff *= BACKOFF_MULTIPLIER
                        continue
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
            except (
                botocore.exceptions.EndpointConnectionError,
                botocore.exceptions.ConnectionClosedError,
            ) as exc:
                if attempt < attempts - 1:
                    await asyncio.sleep(backoff)
                    backoff *= BACKOFF_MULTIPLIER
                    continue
                raise ProviderConnectionError(
                    provider="bedrock",
                    endpoint=endpoint,
                    details=str(exc),
                ) from exc
            else:
                return response

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
    reject_truncated: bool = False,
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
    text_parts = [block["text"] for block in content_blocks if "text" in block]
    if reject_truncated:
        raise_if_truncated(
            "bedrock", "", response.get("stopReason", ""), "\n".join(text_parts)
        )

    if not content_blocks:
        raise ProviderEmptyContentError(
            provider="bedrock",
            endpoint="",
            details="Empty content in Bedrock converse response",
        )

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
