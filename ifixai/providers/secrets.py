import os
import re
from re import Pattern
from typing import Any, Final

ENV_VAR_BY_PROVIDER: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "atlascloud": "ATLASCLOUD_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "bedrock": "AWS_ACCESS_KEY_ID",
    "huggingface": "HF_TOKEN",
}

_SECRET_PATTERNS: Final[tuple[Pattern[str], ...]] = (
    re.compile(r"^sk-cp-[A-Za-z0-9_-]+$"),
    re.compile(r"^sk-or-[A-Za-z0-9_-]{20,}$"),
    re.compile(r"^ak-[A-Za-z0-9_-]{20,}$"),
    re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$"),
    re.compile(r"^sk-[A-Za-z0-9_-]{20,}$"),
    re.compile(r"^anthropic_[A-Za-z0-9_-]{20,}$"),
    re.compile(r"^AKIA[0-9A-Z]{16}$"),
    re.compile(r"^hf_[A-Za-z0-9]{20,}$"),
    re.compile(r"^AIzaSy[0-9A-Za-z_-]{33}$"),
    re.compile(r"^FwoGZXIvYXdz[A-Za-z0-9+/=_-]{20,}$"),
)

_SCRUB_RULES: Final[tuple[tuple[Pattern[str], str], ...]] = (
    (
        re.compile(r"sk-cp-[A-Za-z0-9_-]+"),
        "***REDACTED_MINIMAX_KEY***",
    ),
    (
        re.compile(r"sk-or-[A-Za-z0-9_-]{20,}"),
        "***REDACTED_OPENROUTER_KEY***",
    ),
    (
        re.compile(r"(?<![A-Za-z0-9_-])ak-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
        "***REDACTED_ATLASCLOUD_KEY***",
    ),
    (
        re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
        "***REDACTED_ANTHROPIC_KEY***",
    ),
    (
        re.compile(r"anthropic_[A-Za-z0-9_-]{20,}"),
        "***REDACTED_ANTHROPIC_KEY***",
    ),
    (
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        "***REDACTED_OPENAI_KEY***",
    ),
    (
        re.compile(r"AIzaSy[0-9A-Za-z_-]{33}"),
        "***REDACTED_GEMINI_KEY***",
    ),
    (
        re.compile(r"FwoGZXIvYXdz[A-Za-z0-9+/=_-]{20,}"),
        "***REDACTED_BEDROCK_SESSION***",
    ),
    (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "***REDACTED_AWS_KEY***",
    ),
    (
        re.compile(r"hf_[A-Za-z0-9]{20,}"),
        "***REDACTED_HUGGINGFACE_KEY***",
    ),
    (
        re.compile(r"\b[a-fA-F0-9]{32}\b"),
        "***REDACTED_AZURE_KEY***",
    ),
    (
        re.compile(
            r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._~+/=-]+",
        ),
        r"\1***REDACTED_BEARER_TOKEN***",
    ),
    (
        re.compile(
            r"(?i)(x-api-key:\s*)[A-Za-z0-9._~+/=-]+",
        ),
        r"\1***REDACTED_API_KEY***",
    ),
)


class SecretLeakError(RuntimeError):
    pass


def get_api_key(provider: str) -> str | None:
    env_var = ENV_VAR_BY_PROVIDER.get(provider.lower())
    if env_var is None:
        return None
    return os.environ.get(env_var)


def looks_like_secret(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) < 20:
        return False
    return any(pat.match(value) for pat in _SECRET_PATTERNS)


def scrub_secrets(text: str) -> str:
    if not isinstance(text, str):
        return text
    scrubbed = text
    for pattern, replacement in _SCRUB_RULES:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


def assert_no_secrets(payload: Any, *, where: str = "<payload>") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert_no_secrets(value, where=f"{where}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            assert_no_secrets(item, where=f"{where}[{index}]")
        return
    if isinstance(payload, str) and looks_like_secret(payload):
        raise SecretLeakError(
            f"refusing to write payload at {where}: value matches a known "
            f"secret pattern (length={len(payload)}); strip the secret before "
            "serialization"
        )
