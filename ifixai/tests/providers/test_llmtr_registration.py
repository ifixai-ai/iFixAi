"""LLMTR is reachable from every surface that names a provider."""

from __future__ import annotations

import pytest

pytest.importorskip("openai")

from ifixai.cli.init import PROVIDER_ENV_KEYS, detect_available_providers
from ifixai.cli.model_catalog import default_model, suggestions
from ifixai.cli.run import PROVIDER_CHOICES
from ifixai.cli.setup_cmd import _ALL_PROVIDERS, _PROVIDER_DESCRIPTIONS, _missing_keys
from ifixai.providers.llmtr import DEFAULT_MODEL, LLMTRProvider
from ifixai.providers.resolver import (
    REGISTERED_PROVIDERS,
    credential_env_vars,
    detect_available_credentials,
    resolve_credential,
    resolve_provider,
    select_cross_provider_judge,
)
from ifixai.reporting.scorecard import grading_vendor

KEY_VARIABLE = "LLMTR_API_KEY"
OWNED_KEY = "owned-synthetic-llmtr-key"


def test_the_name_resolves_to_the_adapter() -> None:
    assert isinstance(resolve_provider("llmtr"), LLMTRProvider)
    assert isinstance(resolve_provider("LLMTR"), LLMTRProvider)


def test_the_cli_and_the_registry_both_accept_the_name() -> None:
    assert "llmtr" in REGISTERED_PROVIDERS
    assert "llmtr" in PROVIDER_CHOICES


def test_the_key_is_read_from_llmtr_s_own_variable() -> None:
    assert credential_env_vars("llmtr") == (KEY_VARIABLE,)
    assert resolve_credential("llmtr", {KEY_VARIABLE: OWNED_KEY}) == OWNED_KEY
    assert resolve_credential("llmtr", {"OPENAI_API_KEY": "owned-other-key"}) is None
    assert PROVIDER_ENV_KEYS["llmtr"] == KEY_VARIABLE


def test_a_gateway_key_alone_never_auto_selects_a_judge() -> None:
    available = detect_available_credentials({KEY_VARIABLE: OWNED_KEY})

    assert available == ["llmtr"]
    assert select_cross_provider_judge("openai", available) is None


def test_the_wizard_offers_the_gateway_with_a_description() -> None:
    assert "llmtr" in _ALL_PROVIDERS
    assert "LLMTR" in _PROVIDER_DESCRIPTIONS["llmtr"]


def test_the_wizard_default_is_the_adapter_default() -> None:
    assert default_model("llmtr") == DEFAULT_MODEL
    assert DEFAULT_MODEL == "qwen/qwen3.8-27b-free"


def test_every_suggested_model_is_a_creator_prefixed_slug() -> None:
    suggested = [model for model, _ in suggestions("llmtr")]

    assert DEFAULT_MODEL in suggested
    assert len(suggested) == len(set(suggested))
    for model in suggested:
        creator, separator, name = model.partition("/")
        assert creator and separator and name, model


def test_missing_and_present_keys_are_explained(monkeypatch) -> None:
    monkeypatch.delenv(KEY_VARIABLE, raising=False)
    assert _missing_keys([("system", "llmtr")]) == [("system", "llmtr", KEY_VARIABLE)]
    monkeypatch.setenv(KEY_VARIABLE, OWNED_KEY)
    assert ("llmtr", KEY_VARIABLE) in detect_available_providers()
    assert _missing_keys([("system", "llmtr")]) == []


def test_grading_uses_the_model_slug_prefix() -> None:
    assert grading_vendor("llmtr", "qwen/qwen3.8-27b-free") == "qwen"
    assert grading_vendor("llmtr", "openai/gpt-4o-mini") == "openai"
    assert grading_vendor("llmtr", None) == "llmtr"
