import asyncio
import logging
from contextlib import closing
from traceback import clear_frames

import boto3
import botocore.exceptions
from botocore.config import Config

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.evaluation.run_stop import count_request
from ifixai.providers.base import (
    RETRYABLE_HTTP_STATUS_CODES,
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
_DEFAULT_REGION = "us-east-1"

_logger = logging.getLogger(__name__)

# Endpoint/model pairs whose API rejected temperature. Process-wide so the SUT,
# judge and connection check share learned support only for the same API.
_NO_TEMPERATURE_MODELS: set[tuple[str | None, str]] = set()
# (endpoint, model, role) already warned about.
_DEFAULT_TEMPERATURE_WARNED: set[tuple[str | None, str, str]] = set()


class BedrockProvider(ChatProvider):

    def __init__(
        self,
        region_name: str | None = None,
        profile_name: str | None = None,
    ) -> None:
        self.region_name = region_name
        self.profile_name = profile_name

    def accepts_temperature(self, model: str | None, endpoint: str | None) -> bool:
        # As far as this run knows: a pair joins _NO_TEMPERATURE_MODELS on its first rejection.
        return (endpoint, model or "") not in _NO_TEMPERATURE_MODELS

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        if not config.model:
            raise ProviderResponseError(
                provider="bedrock",
                endpoint=f"bedrock-runtime.{self.region_name or _DEFAULT_REGION}.amazonaws.com",
                details=(
                    "Bedrock model ID is required. "
                    "Set config.model to a Bedrock model ID "
                    "(e.g., 'anthropic.claude-3-sonnet-20240229-v1:0')."
                ),
            )

        # Without an explicit region, honor the standard AWS chain
        # (AWS_REGION / AWS_DEFAULT_REGION / profile) before the default.
        session_kwargs: dict = {}
        if self.region_name:
            session_kwargs["region_name"] = self.region_name
        if self.profile_name:
            session_kwargs["profile_name"] = self.profile_name

        session = boto3.Session(**session_kwargs)
        region = getattr(session, "region_name", None) or _DEFAULT_REGION
        endpoint = config.endpoint or f"bedrock-runtime.{region}.amazonaws.com"

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
            "region_name": region,
            "config": Config(
                retries={"total_max_attempts": 1},
                **socket_limits,
            ),
        }
        if config.endpoint:
            client_kwargs["endpoint_url"] = config.endpoint

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
            count_request()
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        _invoke_owned_converse,
                        session,
                        client_kwargs,
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
                    "InvalidSignatureException",
                    "ExpiredTokenException",
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
                ) or http_status in RETRYABLE_HTTP_STATUS_CODES:
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
                _warn_once_at_default_temperature(config.endpoint, config.model, config.role)
                return response

        raise ProviderRateLimitError(
            provider="bedrock",
            endpoint=endpoint,
            details="Exhausted all retry attempts",
        )



def _invoke_owned_converse(
    session: boto3.Session,
    client_kwargs: dict,
    model_id: str,
    system_prompts: list[dict],
    messages: list[dict],
    inference_config: dict,
    reject_truncated: bool = False,
) -> str:
    # The blocking worker owns the client: an asyncio timeout or cancellation
    # cannot stop its socket operation, so only this worker may close it.
    try:
        with closing(session.client(**client_kwargs)) as client:
            return _invoke_converse(
                client, model_id, system_prompts, messages, inference_config,
                reject_truncated, client_kwargs.get("endpoint_url"),
            )
    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as exc:
        # Completed SDK frames retain response/pool objects through the error.
        # Release those locals, retaining the native error and traceback sites.
        clear_frames(exc.__traceback__)
        raise


def _invoke_converse(
    client: object,
    model_id: str,
    system_prompts: list[dict],
    messages: list[dict],
    inference_config: dict,
    reject_truncated: bool = False,
    endpoint: str | None = None,
) -> str:
    converse_kwargs: dict = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": inference_config,
    }
    if system_prompts:
        converse_kwargs["system"] = system_prompts

    response = _converse(client, endpoint, converse_kwargs)

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

    if not any(text_parts):
        raise ProviderEmptyContentError(
            provider="bedrock",
            endpoint="",
            details="Empty text blocks in Bedrock converse response",
        )

    return "\n".join(text_parts)


def _converse(client: object, endpoint: str | None, converse_kwargs: dict) -> dict:
    # Newer Claude models reject any temperature: retry once without it and remember the model.
    inference_config = converse_kwargs["inferenceConfig"]
    temperature = inference_config["temperature"]
    without_temperature = {
        **converse_kwargs,
        "inferenceConfig": {k: v for k, v in inference_config.items() if k != "temperature"},
    }
    capability_key = (endpoint, converse_kwargs["modelId"])
    if capability_key not in _NO_TEMPERATURE_MODELS:
        try:
            return client.converse(**converse_kwargs)  # type: ignore[attr-defined]
        except botocore.exceptions.ClientError as exc:
            error = exc.response.get("Error", {})
            # Outside 0..1 the error is our bad value, not a model that refuses temperature.
            if (
                error.get("Code") != "ValidationException"
                or "temperature" not in error.get("Message", "").lower()
                or not 0 <= temperature <= 1
            ):
                raise
            _NO_TEMPERATURE_MODELS.add(capability_key)
    return client.converse(**without_temperature)  # type: ignore[attr-defined]


def _warn_once_at_default_temperature(endpoint: str | None, model: str, role: str) -> None:
    warned_key = (endpoint, model, role)
    if (endpoint, model) not in _NO_TEMPERATURE_MODELS or warned_key in _DEFAULT_TEMPERATURE_WARNED:
        return
    _DEFAULT_TEMPERATURE_WARNED.add(warned_key)
    _logger.warning(
        "%s model %s rejects temperature; the %s runs at the model's default temperature",
        role,
        model,
        role,
    )


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
