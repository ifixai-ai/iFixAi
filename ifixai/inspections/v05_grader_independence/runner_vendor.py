"""Which developer a provider configuration resolves to (V05).

V05's independence floor asks whether the grader and the system under test are KIN — sibling models
from one developer. That needs a declared identity rather than a guess from a model string, so the
`(provider, model) -> vendor slug` step is not re-derived here: the scorecard's `grading_vendor`
already owns it for the self-judge-bias check, including aggregator providers (where the vendor is the
model's prefix) and the azure->openai / gemini->google aliases.
"""

from dataclasses import dataclass

from ifixai.core.types import ProviderConfig
from ifixai.reporting.scorecard import grading_vendor

# Vendor slug -> (developer, product family). A slug absent from this map is the unresolvable case:
# `bedrock` and `huggingface` are HOSTS serving other vendors' models, and a bare `http` endpoint
# declares no vendor at all, so none of them is evidence of kinship.
VENDOR_IDENTITIES: dict[str, tuple[str, str]] = {
    "anthropic": ("Anthropic", "Claude"),
    "openai": ("OpenAI", "ChatGPT"),
    "google": ("Google", "Gemini"),
    "meta": ("Meta", "Llama"),
    "mistral": ("Mistral AI", "Le Chat"),
    "mistralai": ("Mistral AI", "Le Chat"),
    "cohere": ("Cohere", "Command"),
    "deepseek": ("DeepSeek", "DeepSeek Chat"),
    "x-ai": ("xAI", "Grok"),
    "xai": ("xAI", "Grok"),
    "qwen": ("Alibaba Cloud", "Qwen"),
    "alibaba": ("Alibaba Cloud", "Qwen"),
    "microsoft": ("Microsoft", "Copilot"),
    "amazon": ("Amazon", "Nova"),
    "ai21": ("AI21 Labs", "Jamba"),
    # Zhipu AI rebranded internationally to Z.ai, so aggregator slugs carry both names.
    "z-ai": ("Z.ai", "GLM"),
    "zhipu": ("Z.ai", "GLM"),
    "zhipuai": ("Z.ai", "GLM"),
    "moonshotai": ("Moonshot AI", "Kimi"),
    "moonshot": ("Moonshot AI", "Kimi"),
    "minimax": ("MiniMax", "MiniMax"),
}


@dataclass(frozen=True)
class VendorIdentity:
    """The developer and product family a provider configuration resolves to."""

    slug: str
    developer: str
    product: str


def resolve_vendor_identity(config: ProviderConfig) -> VendorIdentity | None:
    """The configuration's developer and product, or None when it names no known vendor."""
    slug = grading_vendor(config.provider, config.model)
    identity = VENDOR_IDENTITIES.get(slug)
    if identity is None:
        return None
    developer, product = identity
    return VendorIdentity(slug=slug, developer=developer, product=product)
