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
        ("openai/gpt-4o", "OpenAI flagship — strong general reasoning"),
        ("openai/gpt-4o-mini", "Cheaper & faster OpenAI model"),
        ("anthropic/claude-3.5-sonnet", "Anthropic — excellent reasoning & safety"),
        ("anthropic/claude-3.7-sonnet", "Newer Anthropic Sonnet"),
        ("google/gemini-2.0-flash-001", "Google — fast and inexpensive"),
        ("meta-llama/llama-3.3-70b-instruct", "Open-weights Llama, good value"),
        ("deepseek/deepseek-chat", "Strong, very low cost"),
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
