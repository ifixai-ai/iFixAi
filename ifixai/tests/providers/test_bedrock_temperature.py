"""A Bedrock model that refuses temperature is retried without it and reported as
running at its default."""

import boto3
import botocore.exceptions
import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderResponseError

# The SDK is an optional extra; without it there is nothing to test.
adapter = pytest.importorskip("ifixai.providers.bedrock")

REJECTS = "us.anthropic.claude-opus-4-7"
TEMPERATURE_400 = "The model returned the following errors: `temperature` is deprecated for this model."


class FakeBedrock:
    """bedrock-runtime client stand-in that records every inferenceConfig."""

    def __init__(self, message: str = TEMPERATURE_400) -> None:
        self.message = message
        self.configs: list[dict] = []

    def converse(self, **kwargs) -> dict:
        self.configs.append(dict(kwargs["inferenceConfig"]))
        if kwargs["modelId"] == REJECTS and "temperature" in kwargs["inferenceConfig"]:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "ValidationException", "Message": self.message},
                 "ResponseMetadata": {"HTTPStatusCode": 400}},
                "Converse",
            )
        return {"output": {"message": {"content": [{"text": "ok"}]}}, "stopReason": "end_turn"}

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def no_known_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter, "_NO_TEMPERATURE_MODELS", set(), raising=False)
    monkeypatch.setattr(adapter, "_DEFAULT_TEMPERATURE_WARNED", set(), raising=False)


def _use(monkeypatch: pytest.MonkeyPatch, client: FakeBedrock) -> None:
    monkeypatch.setattr(boto3.Session, "client", lambda self, **kwargs: client)


def _config(model: str = REJECTS, **overrides) -> ProviderConfig:
    return ProviderConfig(provider="bedrock", api_key="", model=model, max_retries=0, **overrides)


async def _send(provider, config: ProviderConfig) -> str:
    return await provider.send_message([ChatMessage(role="user", content="hi")], config)


@pytest.mark.asyncio
async def test_rejected_temperature_is_dropped_and_remembered(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = FakeBedrock()
    _use(monkeypatch, client)
    provider = adapter.BedrockProvider()
    assert provider.accepts_temperature(REJECTS, None)

    assert await _send(provider, _config(max_tokens=50)) == "ok"
    assert await _send(provider, _config(max_tokens=50)) == "ok"

    assert client.configs == [
        {"temperature": 0.0, "maxTokens": 50},
        {"maxTokens": 50},
        {"maxTokens": 50},
    ]
    assert not provider.accepts_temperature(REJECTS, None)
    assert caplog.text.count("rejects temperature") == 1


@pytest.mark.asyncio
async def test_other_models_keep_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeBedrock()
    _use(monkeypatch, client)
    provider = adapter.BedrockProvider()

    await _send(provider, _config(model="anthropic.claude-3-haiku-20240307-v1:0"))

    assert client.configs == [{"temperature": 0.0}]
    assert provider.accepts_temperature("anthropic.claude-3-haiku-20240307-v1:0", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "temperature"),
    [("Input is too long for requested model.", 0.0), (TEMPERATURE_400, 1.5)],
)
async def test_other_validation_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, message: str, temperature: float
) -> None:
    client = FakeBedrock(message)
    _use(monkeypatch, client)
    provider = adapter.BedrockProvider()

    with pytest.raises(ProviderResponseError):
        await _send(provider, _config(temperature=temperature))

    assert len(client.configs) == 1
    assert provider.accepts_temperature(REJECTS, None)
