"""Cloudflare AI Gateway is reachable from every surface that names a provider.

A provider that resolves but is missing from one list is a silent gap: the CLI
rejects the flag, the wizard never offers it, a leaked token is written to a
report unredacted, or a same-vendor judge is reported as independent.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

pytest.importorskip("openai")

from ifixai.cli.init import (
    PROVIDER_COMPANION_ENV_KEYS,
    PROVIDER_ENV_KEYS,
    detect_available_providers,
    init,
)
from ifixai.cli.model_catalog import default_model, suggestions
from ifixai.cli.run import PROVIDER_CHOICES
from ifixai.cli.setup_cmd import _ALL_PROVIDERS, _PROVIDER_DESCRIPTIONS, _missing_keys
from ifixai.core.types import ProviderConfig
from ifixai.inspections.v05_grader_independence.runner_vendor import (
    resolve_vendor_identity,
)
from ifixai.judge.config import JudgeConfig
from ifixai.providers.cloudflare import (
    ACCOUNT_ID_ENV_VAR,
    DEFAULT_MODEL,
    CloudflareAIGatewayProvider,
)
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
from ifixai.reporting.scorecard import (
    grading_vendor,
    self_judge_bias_applies,
)

# Assembled at import time so the source holds no token-shaped literal for the
# repo's secret scanner (gitleaks, see .pre-commit-config.yaml) to flag.
TOKEN_BODY = "syntheticRegistration" * 2 + "0a1b2c3d"
USER_TOKEN = "cfut_" + TOKEN_BODY
TOKEN_VARIABLE = "CLOUDFLARE_API_TOKEN"
ACCOUNT_ID = "0123456789abcdef" * 2
WORKERS_AI_LLAMA = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"


def test_the_name_resolves_to_the_adapter() -> None:
    assert isinstance(resolve_provider("cloudflare"), CloudflareAIGatewayProvider)
    assert isinstance(resolve_provider("Cloudflare"), CloudflareAIGatewayProvider)


def test_the_cli_and_the_registry_both_accept_the_name() -> None:
    assert "cloudflare" in REGISTERED_PROVIDERS
    assert "cloudflare" in PROVIDER_CHOICES


def test_the_token_is_read_from_cloudflare_s_own_variable() -> None:
    assert credential_env_vars("cloudflare") == (TOKEN_VARIABLE,)
    assert resolve_credential("cloudflare", {TOKEN_VARIABLE: USER_TOKEN}) == USER_TOKEN
    assert resolve_credential("cloudflare", {"OPENAI_API_KEY": "sk-other"}) is None
    assert PROVIDER_ENV_KEYS["cloudflare"] == TOKEN_VARIABLE


def test_a_gateway_token_alone_never_auto_selects_a_judge() -> None:
    """Pinned judge only: one token fronts every vendor, so nothing can be inferred
    about which model would be independent of the system under test."""
    available = detect_available_credentials({TOKEN_VARIABLE: USER_TOKEN})

    assert available == ["cloudflare"]
    assert select_cross_provider_judge("openai", available) is None


def test_the_wizard_offers_the_gateway_with_a_description() -> None:
    assert "cloudflare" in _ALL_PROVIDERS
    assert "Cloudflare AI Gateway" in _PROVIDER_DESCRIPTIONS["cloudflare"]


def test_the_wizard_default_is_the_adapter_default() -> None:
    assert default_model("cloudflare") == DEFAULT_MODEL


def test_every_suggested_model_is_an_author_prefixed_id() -> None:
    """Third-party models are `provider/model`; Workers AI adds an `@cf/` namespace."""
    suggested = [model for model, _ in suggestions("cloudflare")]

    assert DEFAULT_MODEL in suggested
    assert len(suggested) == len(set(suggested))
    for model in suggested:
        slug = model.removeprefix("workers-ai/").removeprefix("@cf/")
        author, separator, name = slug.partition("/")
        assert author and separator and name, model
        assert "/" not in name, model


def test_the_wizard_names_the_account_variable_beside_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The token alone cannot make a call: the account is part of the URL."""
    monkeypatch.delenv(TOKEN_VARIABLE, raising=False)
    monkeypatch.delenv(ACCOUNT_ID_ENV_VAR, raising=False)

    missing = _missing_keys([("system under test", "cloudflare")])

    assert PROVIDER_COMPANION_ENV_KEYS["cloudflare"] == (ACCOUNT_ID_ENV_VAR,)
    assert [variable for _, _, variable in missing] == [
        TOKEN_VARIABLE,
        ACCOUNT_ID_ENV_VAR,
    ]


def test_the_wizard_asks_for_nothing_once_both_variables_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_VARIABLE, USER_TOKEN)
    monkeypatch.setenv(ACCOUNT_ID_ENV_VAR, ACCOUNT_ID)

    selected = [("system under test", "cloudflare"), ("judge #1", "cloudflare")]

    assert _missing_keys(selected) == []


def test_a_wrangler_token_never_outranks_a_model_vendor_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLOUDFLARE_API_TOKEN is usually set for wrangler or Terraform, so `ifixai
    init` must not suggest the gateway while a model vendor's key is present."""
    for variable in PROVIDER_ENV_KEYS.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv(TOKEN_VARIABLE, USER_TOKEN)
    monkeypatch.setenv(ACCOUNT_ID_ENV_VAR, ACCOUNT_ID)
    monkeypatch.setenv("HF_TOKEN", "owned-synthetic-huggingface-token")

    detected = [provider for provider, _ in detect_available_providers()]

    assert detected == ["huggingface", "cloudflare"]


def test_a_token_without_an_account_is_not_offered_as_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise `ifixai init` recommends a command that stops at its first call."""
    for variable in PROVIDER_ENV_KEYS.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv(ACCOUNT_ID_ENV_VAR, raising=False)
    monkeypatch.setenv(TOKEN_VARIABLE, USER_TOKEN)

    assert detect_available_providers() == []

    monkeypatch.setenv(ACCOUNT_ID_ENV_VAR, ACCOUNT_ID)

    assert detect_available_providers() == [("cloudflare", TOKEN_VARIABLE)]


def test_init_lists_the_account_variable_beside_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in PROVIDER_ENV_KEYS.values():
        monkeypatch.delenv(variable, raising=False)

    result = CliRunner().invoke(init, ["--non-interactive"])

    assert result.exit_code == 0, result.output
    assert (
        f"{TOKEN_VARIABLE} + {ACCOUNT_ID_ENV_VAR}  (for --provider cloudflare)"
        in result.output
    )
    assert "OPENAI_API_KEY  (for --provider openai)" in result.output


@pytest.mark.parametrize("prefix", ["cfut_", "cfat_", "cfk_"])
def test_every_cloudflare_credential_class_is_redacted(prefix: str) -> None:
    """User tokens, account tokens and global keys share one prefixed format."""
    credential = prefix + TOKEN_BODY
    leaked = f"Illegal header value b'Bearer {credential}'"

    scrubbed = scrub_secrets(leaked)

    assert credential not in scrubbed
    assert "***REDACTED_CLOUDFLARE_TOKEN***" in scrubbed


def test_a_token_is_refused_in_a_serialized_payload() -> None:
    assert looks_like_secret(USER_TOKEN)
    with pytest.raises(SecretLeakError):
        assert_no_secrets({"config": {"api_key": USER_TOKEN}})


def test_the_gateway_s_own_authorization_header_is_redacted() -> None:
    """An older unprefixed token has no shape a pattern can find, so the header
    rule is the only thing between it and a log."""
    older_token = "syntheticOlderFormat" * 2
    echoed = f"cf-aig-authorization: Bearer {older_token}"

    assert older_token not in scrub_secrets(echoed)


def test_a_token_glued_to_other_text_is_still_redacted() -> None:
    glued = f"x-{USER_TOKEN}"

    assert USER_TOKEN not in scrub_secrets(glued)


def test_an_older_unprefixed_token_is_redacted_only_in_an_authorization_header() -> None:
    """It is forty characters with no fixed shape, so no pattern can find it."""
    older_token = "syntheticOlderFormat" * 2

    assert older_token not in scrub_secrets(f"Authorization: Bearer {older_token}")
    assert older_token in scrub_secrets(f"token was {older_token}")
    assert not looks_like_secret(older_token)


def test_an_ordinary_word_starting_with_the_prefix_is_left_alone() -> None:
    harmless = "the cfut_ prefix marks a user token"

    assert scrub_secrets(harmless) == harmless
    assert not looks_like_secret("cfut_short")


@pytest.mark.parametrize(
    ("model", "vendor"),
    [
        ("openai/gpt-4.1-mini", "openai"),
        ("anthropic/claude-haiku-4.5", "anthropic"),
        ("google-ai-studio/gemini-2.5-flash", "google"),
        ("grok/grok-4", "xai"),
        (WORKERS_AI_LLAMA, "meta"),
        (f"workers-ai/{WORKERS_AI_LLAMA}", "meta"),
        ("@cf/moonshotai/kimi-k2.6", "moonshotai"),
        ("@cf/openai/gpt-oss-120b", "openai"),
        ("@cf/google/gemma-4-26b-a4b-it", "google"),
        ("@hf/google/gemma-7b-it", "google"),
        ("@cf/meta-llama/llama-2-7b-chat-hf-lora", "meta"),
        ("@cf/deepseek-ai/deepseek-v4-flash-0731", "deepseek"),
        ("@cf/zai-org/glm-5.2", "z-ai"),
    ],
)
def test_the_grading_vendor_is_the_model_s_author(model: str, vendor: str) -> None:
    """The gateway routes Google and xAI under its own provider names; a Workers AI
    id leads with a catalog namespace, not the author, and names some authors by
    their Hugging Face organisation."""
    assert grading_vendor("cloudflare", model) == vendor


@pytest.mark.parametrize(
    ("provider", "model", "vendor"),
    [
        ("openrouter", "openai/gpt-4o", "openai"),
        ("requesty", "anthropic/claude-sonnet-4-6", "anthropic"),
        ("openrouter", "google/gemini-2.5-flash", "google"),
        ("requesty", "xai/grok-4", "xai"),
        ("requesty", "mistral/mistral-large", "mistral"),
        ("openrouter", "deepseek/deepseek-chat-v3.1", "deepseek"),
        ("openrouter", "z-ai/glm-5.2", "z-ai"),
        ("litellm", "bedrock/claude-sonnet-4-6@eu-central-1", "anthropic"),
        ("litellm", "vertex_ai/gemini-2.5-pro", "google"),
        ("openai", "gpt-4o", "openai"),
        ("azure", "my-deployment", "openai"),
        ("gemini", "gemini-2.0-flash", "google"),
        ("huggingface", "meta-llama/Llama-3.3-70B-Instruct", "huggingface"),
        ("http", None, "http"),
        ("openrouter", None, "openrouter"),
    ],
)
def test_every_other_provider_resolves_to_the_vendor_it_did_before(
    provider: str, model: str | None, vendor: str
) -> None:
    """Recorded on the branch point before the Workers AI change."""
    assert grading_vendor(provider, model) == vendor


@pytest.mark.parametrize(
    ("provider", "model", "vendor"),
    [
        ("openrouter", "x-ai/grok-4", "xai"),
        ("openrouter", "mistralai/mistral-large", "mistral"),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct", "meta"),
        ("atlascloud", "deepseek-ai/deepseek-v3.1", "deepseek"),
        ("atlascloud", "zai-org/glm-4.6", "z-ai"),
    ],
)
def test_one_vendor_spelled_two_ways_resolves_to_one_slug(
    provider: str, model: str, vendor: str
) -> None:
    """The five results this change alters for other gateways, on purpose: the
    same vendor has to read the same whichever gateway serves it. Before, these
    resolved to `x-ai`, `mistralai`, `meta-llama`, `deepseek-ai` and `zai-org`."""
    assert grading_vendor(provider, model) == vendor


@pytest.mark.parametrize(
    ("model", "developer"),
    [
        (WORKERS_AI_LLAMA, "Meta"),
        ("@cf/deepseek-ai/deepseek-v4-flash-0731", "DeepSeek"),
        ("@cf/zai-org/glm-5.2", "Z.ai"),
        ("@cf/moonshotai/kimi-k2.6", "Moonshot AI"),
        ("openai/gpt-4.1-mini", "OpenAI"),
        ("google-ai-studio/gemini-2.5-flash", "Google"),
        ("grok/grok-4", "xAI"),
    ],
)
def test_the_grader_independence_inspection_knows_the_developer(
    model: str, developer: str
) -> None:
    """V05 reads an unresolved vendor as independent, so it has to resolve."""
    identity = resolve_vendor_identity(
        ProviderConfig(provider="cloudflare", model=model)
    )

    assert identity is not None
    assert identity.developer == developer


@pytest.mark.parametrize(
    ("sut_model", "judge_provider", "judge_model"),
    [
        (WORKERS_AI_LLAMA, "openrouter", "meta-llama/llama-3.3-70b-instruct"),
        (
            "@cf/deepseek-ai/deepseek-v4-flash-0731",
            "openrouter",
            "deepseek/deepseek-chat-v3.1",
        ),
        ("@cf/zai-org/glm-5.2", "openrouter", "z-ai/glm-5.2"),
        ("@hf/google/gemma-7b-it", "gemini", "gemini-2.0-flash"),
        ("google-ai-studio/gemini-2.5-flash", "gemini", "gemini-2.0-flash"),
        ("grok/grok-4", "requesty", "xai/grok-4"),
        ("grok/grok-4", "openrouter", "x-ai/grok-4"),
        ("xai/grok-4.5", "openrouter", "x-ai/grok-4"),
        ("grok/grok-4", "cloudflare", "xai/grok-4.5"),
        ("@cf/mistral/mistral-7b-instruct-v0.2-lora", "openrouter", "mistralai/mistral-large"),
        (
            "@cf/mistral/mistral-7b-instruct-v0.2-lora",
            "cloudflare",
            "@cf/mistralai/mistral-small-3.1-24b-instruct",
        ),
    ],
)
def test_the_same_vendor_on_another_gateway_is_not_an_independent_judge(
    sut_model: str, judge_provider: str, judge_model: str
) -> None:
    judge = JudgeConfig(provider=judge_provider, model=judge_model)

    assert self_judge_bias_applies(judge, "cloudflare", sut_model) is True


@pytest.mark.parametrize(
    ("sut_provider", "sut_model", "judge_model", "is_biased"),
    [
        (
            "cloudflare",
            "google-ai-studio/gemini-2.5-flash",
            "anthropic/claude-haiku-4.5",
            False,
        ),
        ("cloudflare", "openai/gpt-4.1", "openai/gpt-4.1-mini", True),
        ("cloudflare", WORKERS_AI_LLAMA, "@cf/moonshotai/kimi-k2.6", False),
        ("cloudflare", WORKERS_AI_LLAMA, "@cf/meta/llama-3.1-8b-instruct-fp8", True),
        ("cloudflare", "openai/gpt-4.1", "@cf/openai/gpt-oss-120b", True),
        ("openai", "gpt-4o", "openai/gpt-4.1-mini", True),
        ("gemini", "gemini-2.0-flash", "@cf/google/gemma-4-26b-a4b-it", True),
        ("anthropic", "claude-sonnet-4-6", WORKERS_AI_LLAMA, False),
    ],
)
def test_judge_independence_follows_the_model_vendor_not_the_gateway(
    sut_provider: str, sut_model: str, judge_model: str, is_biased: bool
) -> None:
    judge = JudgeConfig(provider="cloudflare", model=judge_model)

    assert self_judge_bias_applies(judge, sut_provider, sut_model) is is_biased
