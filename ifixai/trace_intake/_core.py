"""Shared limits, strict JSON, digests, timestamps and the import-warning collector.

Everything here is passive: it reads bytes already on disk and never resolves, fetches or
executes anything named inside them.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

MAX_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 100000
MAX_WARNINGS = 10000
MAX_WARNING_CHARS = 3000
MAX_LABEL_CHARS = 1000
DEFAULT_MAX_TEXT = 200
MAX_TIMESTAMP = 2**63 - 1

IDENTIFIER_RE = re.compile(r"^\S(?:[\s\S]*\S)?$")
TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
TRUNCATION_RE = re.compile(r"[\[\(<]\s*(?:truncated|redacted|omitted)\b", re.IGNORECASE)
ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d{1,9}))?"
    r"(Z|z|[+-]\d{2}:?\d{2})?$"
)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class TraceIntakeError(ValueError):
    """The input is refused: malformed, oversized or impossible to bind to a capture."""


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise TraceIntakeError("duplicate JSON keys are not accepted")
        value[key] = child
    return value


def reject_constant(name: str) -> None:
    raise TraceIntakeError(f"nonfinite JSON constant {name} is not accepted")


def finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise TraceIntakeError(f"nonfinite JSON number {text} is not accepted")
    return value


def strict_json_loads(text: str) -> Any:
    """Parse JSON refusing duplicate keys and nonfinite numbers."""
    return json.loads(
        text,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_constant,
        parse_float=finite_float,
    )


def read_bounded(path: str | Path) -> str:
    """At most MAX_BYTES of UTF-8; a larger input is refused, never truncated."""
    with Path(path).open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise TraceIntakeError("input exceeds the 64 MiB import limit")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TraceIntakeError("input is not UTF-8") from error


def parse_document(text: str) -> Any:
    """One JSON document, or JSON Lines (one object per newline-separated row)."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise TraceIntakeError("input exceeds the 64 MiB import limit")
    try:
        return strict_json_loads(text)
    except json.JSONDecodeError as whole_error:
        rows: list[Any] = []
        for line in io.StringIO(text, newline="\n"):
            if not line.strip():
                continue
            try:
                rows.append(strict_json_loads(line))
            except json.JSONDecodeError:
                raise TraceIntakeError(
                    f"input is neither JSON nor JSON Lines: {whole_error.msg}"
                ) from whole_error
            if len(rows) > MAX_EVENTS:
                raise TraceIntakeError(f"input exceeds the {MAX_EVENTS} record limit")
        if not rows:
            raise TraceIntakeError("input is empty") from whole_error
        return rows


def canonical_json(value: Any) -> str:
    """Sorted keys, no incidental whitespace, no NaN: one byte string per value."""
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def canonical_digest(value: Any) -> str:
    """Unkeyed SHA-256 of `canonical_json(value)`; the same digest the recorder uses."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def short_hash(value: str, length: int = 24) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def is_identifier(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 200 and bool(IDENTIFIER_RE.match(value))


def iso_to_ns(value: Any) -> int | None:
    """An ISO-8601 timestamp as Unix nanoseconds; naive values are read as UTC."""
    if not isinstance(value, str):
        return None
    match = ISO_RE.match(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    try:
        moment = datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    if zone and zone not in ("Z", "z"):
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        moment -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    delta = moment - EPOCH
    nanos = (delta.days * 86400 + delta.seconds) * 10**9
    nanos += int((fraction or "0").ljust(9, "0"))
    return nanos if 0 <= nanos <= MAX_TIMESTAMP else None


def seconds_to_ns(value: Any) -> int | None:
    """Unix seconds (int, float or decimal string) as exact nanoseconds."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        nanos = int(Decimal(str(value)) * 10**9)
    except (InvalidOperation, ValueError):
        return None
    return nanos if 0 <= nanos <= MAX_TIMESTAMP else None


def cap_text(text: str, max_text: int) -> str:
    """At most `max_text` characters, stripped; labels never carry whole bodies."""
    return text.strip()[: min(max_text, MAX_LABEL_CHARS)].strip()


def has_truncation_marker(value: Any, depth: int = 0) -> bool:
    """True when the content says it was already cut, redacted or omitted upstream."""
    if depth > 32:
        return False
    if isinstance(value, str):
        return bool(TRUNCATION_RE.search(value))
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("truncated", "is_truncated") and child is True:
                return True
            if has_truncation_marker(child, depth + 1):
                return True
    if isinstance(value, list):
        return any(has_truncation_marker(child, depth + 1) for child in value)
    return False


@dataclass
class Warnings:
    """Import gaps aggregated by code, each rendered once with a count and first location."""

    entries: dict[str, list[Any]] = field(default_factory=dict)

    def add(self, code: str, message: str, where: str | None = None) -> None:
        entry = self.entries.get(code)
        if entry is None:
            self.entries[code] = [message, 1, where]
        else:
            entry[1] += 1

    def __bool__(self) -> bool:
        return bool(self.entries)

    def __contains__(self, code: object) -> bool:
        return code in self.entries

    def render(self) -> list[str]:
        rendered = []
        for code, (message, count, where) in self.entries.items():
            text = f"[{code}] {message}"
            if count > 1:
                text += f" ({count} occurrences)"
            if where:
                text += f"; first at {where}"
            rendered.append(text[:MAX_WARNING_CHARS])
        return rendered[:MAX_WARNINGS]
