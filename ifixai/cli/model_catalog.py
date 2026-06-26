"""Curated model suggestions per provider for the setup wizard."""

from __future__ import annotations

DEFAULT_MODEL: dict[str, str] = {
    "openrouter": "openai/gpt-4o",
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-latest",
    "gemini": "gemini-2.0-flash",
    "azure": "gpt-4o",
    "bedrock": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "huggingface": "meta-llama/Llama-3.3-70B-Instruct",
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
}


def default_model(provider: str) -> str | None:
    return DEFAULT_MODEL.get(provider.lower())


def suggestions(provider: str) -> list[tuple[str, str]]:
    return MODEL_SUGGESTIONS.get(provider.lower(), [])
