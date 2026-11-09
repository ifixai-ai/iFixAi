"""Default models must not be ones the vendor has already shut down."""

import pytest

from ifixai.cli.model_catalog import default_model, suggestions

RETIRED_MODELS = {
    "gemini-2.0-flash",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
}


def test_gemini_provider_default_is_not_retired() -> None:
    pytest.importorskip("google.generativeai")
    from ifixai.providers import gemini

    assert gemini.DEFAULT_MODEL not in RETIRED_MODELS
    assert default_model("gemini") == gemini.DEFAULT_MODEL


def test_gemini_and_bedrock_catalog_defaults_are_not_retired() -> None:
    assert default_model("gemini") not in RETIRED_MODELS
    assert default_model("bedrock") not in RETIRED_MODELS


def test_gemini_and_bedrock_suggestions_are_not_retired() -> None:
    for provider in ("gemini", "bedrock"):
        assert not RETIRED_MODELS & {model for model, _ in suggestions(provider)}


# Model ids the vendors have already retired or shut down (each checked
# against the vendor's own deprecations page).
SHUT_DOWN_MODELS = {
    "o3-mini",
    "o4-mini",
    "o1",
    "gpt-4-turbo",
    "gpt-4",
    "claude-3-5-sonnet-latest",
    "claude-3-7-sonnet-latest",
    "claude-3-5-haiku-latest",
    "claude-opus-4-1",
    "deepseek-r1",
    "gemini-1.5-pro",
}


@pytest.mark.parametrize("provider", ["openai", "openrouter", "anthropic", "gemini"])
def test_suggestions_do_not_offer_shut_down_models(provider: str) -> None:
    offered = {model.split("/", 1)[-1] for model, _ in suggestions(provider)}
    assert not SHUT_DOWN_MODELS & offered
