"""Vercel AI Gateway is reachable from every surface that names a provider.

A provider that resolves but is missing from one list is a silent gap: the CLI
rejects the flag, the wizard never offers it, a leaked key is written to a
report unredacted, or a same-vendor judge is reported as independent.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openai")

from ifixai.cli.init import PROVIDER_ENV_KEYS
from ifixai.cli.model_catalog import default_model, suggestions
from ifixai.cli.run import PROVIDER_CHOICES
from ifixai.cli.setup_cmd import _ALL_PROVIDERS, _PROVIDER_DESCRIPTIONS
from ifixai.judge.config import JudgeConfig
from ifixai.providers.resolver import (
    REGISTERED_PROVIDERS,
    credential_env_vars,
    detect_available_credentials,
    resolve_credential,
    resolve_provider,
    select_cross_provider_judge,
)
from ifixai.providers.secrets import (
    SecretLeakError,
    assert_no_secrets,
    looks_like_secret,
    scrub_secrets,
)
from ifixai.providers.vercel import DEFAULT_MODEL, VercelAIGatewayProvider
from ifixai.reporting.scorecard import (
    grading_vendor,
    self_judge_bias_applies,
)

# Assembled at import time so the source holds no key-shaped literal for the
# repo's secret scanner (gitleaks, see .pre-commit-config.yaml) to flag.
GATEWAY_KEY = "vck_" + "syntheticRegistration" * 2
KEY_VARIABLE = "AI_GATEWAY_API_KEY"


def test_the_name_resolves_to_the_adapter() -> None:
    assert isinstance(resolve_provider("vercel"), VercelAIGatewayProvider)
    assert isinstance(resolve_provider("Vercel"), VercelAIGatewayProvider)


def test_the_cli_and_the_registry_both_accept_the_name() -> None:
    assert "vercel" in REGISTERED_PROVIDERS
    assert "vercel" in PROVIDER_CHOICES


def test_the_key_is_read_from_vercel_s_own_variable() -> None:
    assert credential_env_vars("vercel") == (KEY_VARIABLE,)
    assert resolve_credential("vercel", {KEY_VARIABLE: GATEWAY_KEY}) == GATEWAY_KEY
    assert resolve_credential("vercel", {"OPENAI_API_KEY": "sk-other"}) is None
    assert PROVIDER_ENV_KEYS["vercel"] == KEY_VARIABLE


def test_a_gateway_key_alone_never_auto_selects_a_judge() -> None:
    """Pinned judge only: one key fronts every vendor, so nothing can be inferred
    about which model would be independent of the system under test."""
    available = detect_available_credentials({KEY_VARIABLE: GATEWAY_KEY})

    assert available == ["vercel"]
    assert select_cross_provider_judge("openai", available) is None


def test_the_wizard_offers_the_gateway_with_a_description() -> None:
    assert "vercel" in _ALL_PROVIDERS
    assert "Vercel AI Gateway" in _PROVIDER_DESCRIPTIONS["vercel"]


def test_the_wizard_default_is_the_adapter_default() -> None:
    assert default_model("vercel") == DEFAULT_MODEL


def test_every_suggested_model_is_a_creator_prefixed_slug() -> None:
    suggested = [model for model, _ in suggestions("vercel")]

    assert DEFAULT_MODEL in suggested
    assert len(suggested) == len(set(suggested))
    for model in suggested:
        creator, separator, name = model.partition("/")
        assert creator and separator and name, model


def test_a_gateway_key_is_redacted_wherever_it_appears() -> None:
    leaked = f"Illegal header value b'Bearer {GATEWAY_KEY}'"

    scrubbed = scrub_secrets(leaked)

    assert GATEWAY_KEY not in scrubbed
    assert "***REDACTED_VERCEL_KEY***" in scrubbed


def test_a_gateway_key_is_refused_in_a_serialized_payload() -> None:
    assert looks_like_secret(GATEWAY_KEY)
    with pytest.raises(SecretLeakError):
        assert_no_secrets({"config": {"api_key": GATEWAY_KEY}})


@pytest.mark.parametrize(
    ("model", "vendor"),
    [
        ("openai/gpt-4o-mini", "openai"),
        ("anthropic/claude-haiku-4.5", "anthropic"),
        ("google/gemini-2.5-flash", "google"),
        ("minimax/minimax-m3", "minimax"),
    ],
)
def test_the_grading_vendor_is_the_slug_s_creator(model: str, vendor: str) -> None:
    assert grading_vendor("vercel", model) == vendor


@pytest.mark.parametrize(
    ("sut_provider", "sut_model", "judge_model", "is_biased"),
    [
        ("vercel", "google/gemini-2.5-flash", "anthropic/claude-haiku-4.5", False),
        ("vercel", "openai/gpt-4o", "openai/gpt-4o-mini", True),
        ("openai", "gpt-4o", "openai/gpt-4o-mini", True),
        ("azure", "my-deployment", "openai/gpt-4o-mini", True),
        ("gemini", "gemini-2.0-flash", "google/gemini-2.5-pro", True),
        ("anthropic", "claude-sonnet-4-6", "anthropic/claude-haiku-4.5", True),
        ("anthropic", "claude-sonnet-4-6", "openai/gpt-4o-mini", False),
    ],
)
def test_judge_independence_follows_the_model_vendor_not_the_gateway(
    sut_provider: str, sut_model: str, judge_model: str, is_biased: bool
) -> None:
    judge = JudgeConfig(provider="vercel", model=judge_model)

    assert self_judge_bias_applies(judge, sut_provider, sut_model) is is_biased
