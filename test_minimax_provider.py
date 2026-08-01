from ifixai.cli.init import PROVIDER_ENV_KEYS
from ifixai.cli.model_catalog import default_model, suggestions
from ifixai.cli.run import PROVIDER_CHOICES
from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.minimax import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    MODEL_METADATA,
    REGIONAL_ENDPOINTS,
    MiniMaxProvider,
    _build_request,
    _extract_response_text,
)
from ifixai.providers.resolver import (
    credential_env_vars,
    detect_available_credentials,
    resolve_credential,
    resolve_provider,
)
from ifixai.providers.secrets import scrub_secrets


def test_minimax_is_registered_with_dedicated_credentials() -> None:
    provider = resolve_provider("minimax")

    assert isinstance(provider, MiniMaxProvider)
    assert credential_env_vars("minimax") == ("MINIMAX_API_KEY",)
    assert (
        resolve_credential(
            "minimax",
            {"MINIMAX_API_KEY": "test-key"},
        )
        == "test-key"
    )
    assert "minimax" in detect_available_credentials({"MINIMAX_API_KEY": "test-key"})
    assert PROVIDER_ENV_KEYS["minimax"] == "MINIMAX_API_KEY"
    assert "minimax" in PROVIDER_CHOICES


def test_minimax_catalog_and_metadata_match_current_models() -> None:
    assert default_model("minimax") == DEFAULT_MODEL
    assert [model_id for model_id, _ in suggestions("minimax")] == [
        "MiniMax-M3",
        "MiniMax-M2.7",
    ]
    assert MODEL_METADATA == {
        "MiniMax-M3": {
            "context_window": 1000000,
            "pricing_usd_per_million_tokens": {
                "input": 0.6,
                "output": 2.4,
                "cache_read": 0.12,
                "cache_write": None,
            },
            "input_modalities": ["text", "image", "video"],
            "thinking": ["adaptive", "disabled"],
        },
        "MiniMax-M2.7": {
            "context_window": 204800,
            "pricing_usd_per_million_tokens": {
                "input": 0.3,
                "output": 1.2,
                "cache_read": 0.06,
                "cache_write": 0.375,
            },
            "input_modalities": ["text"],
            "thinking": ["always_on"],
        },
    }


def test_minimax_regional_endpoints_are_complete() -> None:
    assert REGIONAL_ENDPOINTS == {
        "global_en": {
            "openai_base_url": "https://api.minimax.io/v1",
            "anthropic_base_url": "https://api.minimax.io/anthropic",
            "docs_root": "https://platform.minimax.io/docs",
        },
        "cn_zh": {
            "openai_base_url": "https://api.minimaxi.com/v1",
            "anthropic_base_url": "https://api.minimaxi.com/anthropic",
            "docs_root": "https://platform.minimaxi.com/docs",
        },
    }
    assert DEFAULT_BASE_URL == REGIONAL_ENDPOINTS["global_en"]["anthropic_base_url"]


def test_minimax_builds_messages_request_for_default_endpoint() -> None:
    config = ProviderConfig(provider="minimax", api_key="test-key")
    messages = [
        ChatMessage(role="system", content="Be precise."),
        ChatMessage(role="user", content="Hello"),
    ]

    endpoint, url, api_style, headers, payload = _build_request(messages, config)

    assert api_style == "messages"
    assert url == f"{endpoint}/v1/messages"
    assert headers["Authorization"] == "Bearer test-key"
    assert payload["model"] == DEFAULT_MODEL
    assert payload["system"] == "Be precise."
    assert payload["messages"] == [{"role": "user", "content": "Hello"}]


def test_minimax_builds_chat_completions_request_for_cn_endpoint() -> None:
    endpoint = REGIONAL_ENDPOINTS["cn_zh"]["openai_base_url"]
    config = ProviderConfig(
        provider="minimax",
        endpoint=endpoint,
        api_key="test-key",
        model="MiniMax-M2.7",
        json_output=True,
    )

    resolved, url, api_style, headers, payload = _build_request(
        [ChatMessage(role="user", content="Hello")],
        config,
    )

    assert resolved == endpoint
    assert api_style == "chat_completions"
    assert url == f"{endpoint}/chat/completions"
    assert headers["Authorization"] == "Bearer test-key"
    assert payload["model"] == "MiniMax-M2.7"
    assert payload["response_format"] == {"type": "json_object"}


def test_minimax_extracts_both_response_styles() -> None:
    assert (
        _extract_response_text(
            {
                "content": [
                    {"type": "thinking", "thinking": "..."},
                    {"type": "text", "text": "Done"},
                ]
            },
            "messages",
            DEFAULT_BASE_URL,
        )
        == "Done"
    )
    assert (
        _extract_response_text(
            {"choices": [{"message": {"content": "Done"}}]},
            "chat_completions",
            REGIONAL_ENDPOINTS["global_en"]["openai_base_url"],
        )
        == "Done"
    )


def test_minimax_subscription_keys_are_scrubbed() -> None:
    assert (
        scrub_secrets("token sk-cp-test-value-for-minimax")
        == "token ***REDACTED_MINIMAX_KEY***"
    )
