"""Curated model suggestions per provider for the setup wizard."""

from __future__ import annotations

from ifixai.providers.minimax import DEFAULT_MODEL as MINIMAX_DEFAULT_MODEL
from ifixai.providers.minimax import MODEL_METADATA as MINIMAX_MODEL_METADATA
from ifixai.providers.minimax import MODEL_PRICING_TIERS as MINIMAX_MODEL_PRICING_TIERS


def _minimax_pricing_summary(model_id: str) -> str:
    tiers = MINIMAX_MODEL_PRICING_TIERS.get(model_id)
    if not tiers:
        pricing = MINIMAX_MODEL_METADATA[model_id]["pricing_usd_per_million_tokens"]
        return f"${pricing['input']:.2f}/${pricing['output']:.2f}"

    summaries = []
    previous_limit = None
    for tier in tiers:
        pricing = tier["pricing_usd_per_million_tokens"]
        limit = tier["max_input_tokens"]
        if limit is not None:
            range_label = f"up to {limit // 1000}k input"
            previous_limit = limit
        elif previous_limit is not None:
            range_label = f"above {previous_limit // 1000}k input"
        else:
            range_label = "all input sizes"
        summaries.append(
            f"${pricing['input']:.2f}/${pricing['output']:.2f} {range_label}"
        )
    return "; ".join(summaries)


DEFAULT_MODEL: dict[str, str] = {
    "openrouter": "openai/gpt-4o",
    "orcarouter": "orcarouter/auto",
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-latest",
    "gemini": "gemini-2.0-flash",
    "azure": "gpt-4o",
    "bedrock": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "huggingface": "meta-llama/Llama-3.3-70B-Instruct",
    "minimax": MINIMAX_DEFAULT_MODEL,
}

MODEL_SUGGESTIONS: dict[str, list[tuple[str, str]]] = {
    "openrouter": [
        ("anthropic/claude-sonnet-4.5", "Anthropic Claude Sonnet 4.5 — strong reasoning & safety"),
        ("anthropic/claude-opus-4.1", "Anthropic Claude Opus 4.1 — most capable, pricier"),
        ("anthropic/claude-haiku-4.5", "Anthropic Claude Haiku 4.5 — fast & cheap"),
        ("openai/gpt-5", "OpenAI GPT-5 — flagship general reasoning"),
        ("openai/gpt-5-mini", "OpenAI GPT-5 Mini — cheaper & faster"),
        ("openai/gpt-4.1", "OpenAI GPT-4.1 — strong, widely available"),
        ("openai/o4-mini", "OpenAI o4 Mini — reasoning-optimized, low cost"),
        ("google/gemini-2.5-pro", "Google Gemini 2.5 Pro — large context, strong reasoning"),
        ("google/gemini-2.5-flash", "Google Gemini 2.5 Flash — fast and inexpensive"),
        ("deepseek/deepseek-r1", "DeepSeek R1 — open reasoning model, very low cost"),
        ("deepseek/deepseek-chat-v3.1", "DeepSeek V3.1 — strong, very low cost"),
        ("meta-llama/llama-4-maverick", "Meta Llama 4 Maverick — open-weights flagship"),
        ("meta-llama/llama-3.3-70b-instruct", "Meta Llama 3.3 70B — open-weights, good value"),
    ],
    "orcarouter": [
        ("orcarouter/auto", "Adaptive auto-routing — frontier or OSS per prompt, zero markup"),
        ("anthropic/claude-opus-4.8", "Anthropic Claude Opus 4.8 — most capable, pricier"),
        ("anthropic/claude-sonnet-4.5", "Anthropic Claude Sonnet 4.5 — strong reasoning & safety"),
        ("openai/gpt-4.1", "OpenAI GPT-4.1 — strong, widely available"),
        ("google/gemini-2.5-flash", "Google Gemini 2.5 Flash — fast and inexpensive"),
        ("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash — very low cost"),
        ("qwen/qwen3-235b-a22b", "Qwen3 235B — capable open-weights instruct"),
    ],
    "openai": [
        ("gpt-4o", "Flagship — strong general reasoning"),
        ("gpt-4o-mini", "Cheaper & faster"),
        ("o3-mini", "Reasoning-optimized, cost-effective"),
        ("gpt-4.1", "Latest large model"),
    ],
    "anthropic": [
        ("claude-3-5-sonnet-latest", "Balanced reasoning & speed"),
        ("claude-3-7-sonnet-latest", "Newer Sonnet"),
        ("claude-3-5-haiku-latest", "Fastest & cheapest Claude"),
    ],
    "gemini": [
        ("gemini-2.0-flash", "Fast and inexpensive"),
        ("gemini-1.5-pro", "Larger context, stronger reasoning"),
    ],
    "azure": [
        ("gpt-4o", "Your Azure deployment name for GPT-4o"),
        ("gpt-4o-mini", "Cheaper/faster deployment"),
    ],
    "bedrock": [
        ("anthropic.claude-3-5-sonnet-20241022-v2:0", "Claude 3.5 Sonnet on Bedrock"),
        ("anthropic.claude-3-5-haiku-20241022-v1:0", "Claude 3.5 Haiku on Bedrock"),
    ],
    "huggingface": [
        ("meta-llama/Llama-3.3-70B-Instruct", "Open-weights Llama 3.3"),
        ("mistralai/Mistral-7B-Instruct-v0.3", "Small, fast Mistral"),
    ],
    "minimax": [
        (
            model_id,
            f"{metadata['context_window']:,}-token context; "
            f"{'/'.join(metadata['input_modalities'])} input; "
            f"{'/'.join(metadata['thinking'])} thinking; "
            f"{_minimax_pricing_summary(model_id)} "
            "per 1M input/output tokens",
        )
        for model_id, metadata in MINIMAX_MODEL_METADATA.items()
    ],
}


def default_model(provider: str) -> str | None:
    return DEFAULT_MODEL.get(provider.lower())


def suggestions(provider: str) -> list[tuple[str, str]]:
    return MODEL_SUGGESTIONS.get(provider.lower(), [])
