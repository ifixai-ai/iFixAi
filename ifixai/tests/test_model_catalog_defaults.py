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
