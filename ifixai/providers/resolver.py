
from pathlib import Path
from typing import Mapping, Union

from ifixai.providers.http import HttpProvider
from ifixai.providers.langchain import LangChainProvider
from ifixai.providers.governance_fixture import GovernanceFixture
from ifixai.providers.governance_mixin import GovernanceMixin
from ifixai.providers.mock_governance import MockGovernanceProvider
from ifixai.providers.bridge import BridgeJudgeProvider

try:
    from ifixai.providers.anthropic import AnthropicProvider
except ImportError:
    AnthropicProvider = None

try:
    from ifixai.providers.azure import AzureOpenAIProvider
except ImportError:
    AzureOpenAIProvider = None

try:
    from ifixai.providers.bedrock import BedrockProvider
except ImportError:
    BedrockProvider = None

try:
    from ifixai.providers.gemini import GeminiProvider
except ImportError:
    GeminiProvider = None

try:
    from ifixai.providers.huggingface import HuggingFaceProvider
except ImportError:
    HuggingFaceProvider = None

try:
    from ifixai.providers.openai import OpenAIProvider
except ImportError:
    OpenAIProvider = None

try:
    from ifixai.providers.openrouter import OpenRouterProvider
except ImportError:
    OpenRouterProvider = None

try:
    from ifixai.providers.litellm import LiteLLMProvider
except ImportError:
    LiteLLMProvider = None


REGISTERED_PROVIDERS: tuple[str, ...] = (
    "http",
    "mock",
    "openai",
    "openrouter",
    "anthropic",
    "gemini",
    "azure",
    "bedrock",
    "huggingface",
    "langchain",
    "litellm",
    "bridge",
)

_MOCK_FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "governance" / "mock.yaml"

_PROVIDER_MAP: dict[str, type] = {
    name: cls
    for name, cls in {
        "http": HttpProvider,
        "openai": OpenAIProvider,
        "openrouter": OpenRouterProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
        "azure": AzureOpenAIProvider,
        "bedrock": BedrockProvider,
        "huggingface": HuggingFaceProvider,
        "langchain": LangChainProvider,
        "litellm": LiteLLMProvider,
        "bridge": BridgeJudgeProvider,
    }.items()
    if cls is not None
}


_GOVERNED_CLASS_CACHE: dict[type, type] = {}


def wrap_with_governance(
    provider: object,
    governance: GovernanceFixture,
) -> object:
    """Compose `GovernanceMixin` onto a live provider instance.

    Synthesizes a subclass `(GovernanceMixin, OriginalCls)` per original
    class (cached) and rebinds the instance's `__class__` so every
    structural hook now reads from `governance` instead of returning
    `None`. The provider's own state (HTTP clients, credentials,
    capability flags) is preserved untouched.
    """
    if isinstance(provider, GovernanceMixin):
        provider._governance = governance
        return provider

    original_cls = type(provider)
    governed_cls = _GOVERNED_CLASS_CACHE.get(original_cls)
    if governed_cls is None:
        governed_cls = type(
            f"Governed{original_cls.__name__}",
            (GovernanceMixin, original_cls),
            {},
        )
        _GOVERNED_CLASS_CACHE[original_cls] = governed_cls
    provider.__class__ = governed_cls
    provider._governance = governance
    return provider


def resolve_provider(provider: Union[str, object]) -> object:
    if not isinstance(provider, str):
        return provider

    name = provider.lower()
    if name == "mock":
        governance = GovernanceFixture.load(str(_MOCK_FIXTURE_PATH))
        return MockGovernanceProvider(governance=governance)

    provider_class = _PROVIDER_MAP.get(name)
    if provider_class is None:
        if name in REGISTERED_PROVIDERS:
            raise ValueError(
                f"Provider '{name}' requires its SDK. "
                f"Install it with: pip install ifixai[{name}]"
            )
        raise ValueError(
            f"Unknown provider: '{name}'. Available: {list(_PROVIDER_MAP.keys())}"
        )
    return provider_class()


_PROVIDER_CREDENTIAL_ENV_VARS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "azure": ("AZURE_OPENAI_API_KEY",),
    "bedrock": ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
    "huggingface": ("HUGGINGFACE_API_TOKEN", "HF_TOKEN"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "litellm": ("LITELLM_API_KEY",),
}

# Providers whose credential is several variables that are ALL required (not
# alternatives) — e.g. bedrock's AWS access-key id + secret. For these, a
# partial set must fail fast at the consent screen, not mid-run.
_PROVIDER_CREDENTIAL_REQUIRES_ALL: frozenset[str] = frozenset({"bedrock"})

_JUDGE_PREFERENCE_ORDER: tuple[str, ...] = (
    "anthropic",
    "openai",
    "gemini",
    "openrouter",
    "azure",
    "bedrock",
    "huggingface",
)


def credential_env_vars(provider: str) -> tuple[str, ...]:
    """The environment variable name(s) that hold a provider's credential.

    The single source of truth (`_PROVIDER_CREDENTIAL_ENV_VARS`) for telling a
    user which env var to set. Empty tuple for an unknown provider.
    """
    return _PROVIDER_CREDENTIAL_ENV_VARS.get(provider.lower(), ())


def credential_requires_all(provider: str) -> bool:
    """True if every variable in `credential_env_vars` is required together
    (e.g. bedrock's AWS pair), rather than being interchangeable alternatives."""
    return provider.lower() in _PROVIDER_CREDENTIAL_REQUIRES_ALL


def resolve_credential(provider: str, environ: Mapping[str, str]) -> str | None:
    """The provider's credential from the environment, or None.

    Env-only by design — keys come from the environment, never the command
    line. Returns None when the provider's variable(s) aren't set, so callers
    can fail with a clear, named message instead of hanging or billing blindly.
    Most providers accept any one of their variables; a require-all provider
    (e.g. bedrock) returns a value only when every variable is present.
    """
    names = credential_env_vars(provider)
    if credential_requires_all(provider):
        values = [environ.get(var) for var in names]
        return values[-1] if all(values) else None
    for var in names:
        value = environ.get(var)
        if value:
            return value
    return None


def detect_available_credentials(environ: Mapping[str, str]) -> list[str]:
    return [
        provider_name
        for provider_name in _PROVIDER_CREDENTIAL_ENV_VARS
        if resolve_credential(provider_name, environ) is not None
    ]


def select_cross_provider_judge(
    sut_provider: str,
    available: list[str],
) -> str | None:
    sut_normalised = sut_provider.lower()
    for candidate in _JUDGE_PREFERENCE_ORDER:
        if candidate in available and candidate != sut_normalised:
            return candidate
    return None
