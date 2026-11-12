"""`import_trace`: one vendor trace export in, one canonical JSONL capture plus warnings out."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ifixai.trace_intake import arize, braintrust, langfuse, langsmith, otel
from ifixai.trace_intake._core import (
    DEFAULT_MAX_TEXT,
    MAX_BYTES,
    MAX_LABEL_CHARS,
    TraceIntakeError,
    Warnings,
    is_identifier,
    parse_document,
    read_bounded,
)
from ifixai.trace_intake.builder import ParsedTrace, build_rows

Parser = Callable[[Any, "str | None", Warnings], ParsedTrace]
PARSERS: dict[str, Parser] = {
    "otel": otel.parse,
    "langfuse": langfuse.parse,
    "langsmith": langsmith.parse,
    "arize": arize.parse,
    "braintrust": braintrust.parse,
}
SOURCES = tuple(PARSERS)


@dataclass(frozen=True)
class ImportResult:
    """Canonical rows (header first, then events), the import's gaps, and the source read."""

    capture_rows: list[dict[str, Any]]
    warnings: list[str]
    source: str

    @property
    def header(self) -> dict[str, Any]:
        return self.capture_rows[0]

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.capture_rows[1:]

    def to_jsonl(self) -> str:
        return "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in self.capture_rows
        )

    def warnings_document(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "trace_id": self.header["trace_id"],
            "capture_complete": self.header["capture_complete"],
            "warnings": list(self.warnings),
        }


def has_openinference(raw: dict[str, Any]) -> bool:
    for resource_span in raw.get("resourceSpans") or []:
        for scope_span in (resource_span or {}).get("scopeSpans") or []:
            for span in (scope_span or {}).get("spans") or []:
                for attribute in (span or {}).get("attributes") or []:
                    key = attribute.get("key") if isinstance(attribute, dict) else None
                    if isinstance(key, str) and key.startswith("openinference."):
                        return True
    return False


def detect_source(raw: Any) -> str:
    """The source format, recognized by structure alone."""
    if isinstance(raw, dict) and isinstance(raw.get("resourceSpans"), list):
        return "arize" if has_openinference(raw) else "otel"
    items = raw
    if isinstance(raw, dict):
        for key in ("data", "runs", "spans", "events"):
            if isinstance(raw.get(key), list):
                items = raw[key]
                break
        else:
            items = [raw]
    if isinstance(items, list) and items and isinstance(items[0], dict):
        first = items[0]
        if "observations" in first:
            return "langfuse"
        if "run_type" in first:
            return "langsmith"
        if "span_attributes" in first and "root_span_id" in first:
            return "braintrust"
        if isinstance(first.get("context"), dict) and "span_id" in first["context"]:
            return "arize"
    raise TraceIntakeError(
        "cannot detect the trace format; pass --from with one of " + ", ".join(SOURCES)
    )


def check_options(tenant_id: str, collector_id: str, max_text: int) -> None:
    if not is_identifier(tenant_id) or not is_identifier(collector_id):
        raise TraceIntakeError("tenant and collector ids must be 1-200 characters, no edge spaces")
    if type(max_text) is not int or not 0 <= max_text <= MAX_LABEL_CHARS:
        raise TraceIntakeError(f"max_text must be between 0 and {MAX_LABEL_CHARS}")


def import_document(
    raw: Any,
    source: str = "auto",
    *,
    tenant_id: str,
    collector_id: str,
    trace_id: str | None = None,
    max_text: int = DEFAULT_MAX_TEXT,
) -> ImportResult:
    """Import an already-parsed export. Tenant and collector come from the caller only."""
    check_options(tenant_id, collector_id, max_text)
    if source == "auto":
        source = detect_source(raw)
    if source not in PARSERS:
        raise TraceIntakeError(f"unknown source {source!r}")
    warnings = Warnings()
    parsed = PARSERS[source](raw, trace_id, warnings)
    rows = build_rows(
        parsed, tenant_id=tenant_id, collector_id=collector_id, max_text=max_text,
        warnings=warnings,
    )
    result = ImportResult(capture_rows=rows, warnings=warnings.render(), source=source)
    if len(result.to_jsonl().encode("utf-8")) > MAX_BYTES:
        raise TraceIntakeError("capture would exceed the 64 MiB import limit")
    return result


def import_trace(
    path: str | Path,
    source: str = "auto",
    *,
    tenant_id: str,
    collector_id: str,
    trace_id: str | None = None,
    max_text: int = DEFAULT_MAX_TEXT,
) -> ImportResult:
    """Read one vendor trace export (at most 64 MiB) and map it to a canonical capture."""
    return import_document(
        parse_document(read_bounded(path)),
        source,
        tenant_id=tenant_id,
        collector_id=collector_id,
        trace_id=trace_id,
        max_text=max_text,
    )


def warnings_path(out: str | Path) -> Path:
    out = Path(out)
    return out.with_name(out.name + ".warnings.json")


def write_result(result: ImportResult, out: str | Path) -> tuple[Path, Path]:
    """Write `<out>` and `<out>.warnings.json`; refuses to replace either file."""
    capture_path, sidecar = Path(out), warnings_path(out)
    for path in (capture_path, sidecar):
        if path.exists():
            raise TraceIntakeError(f"refusing to overwrite {path}")
    with capture_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(result.to_jsonl())
    with sidecar.open("x", encoding="utf-8") as handle:
        json.dump(result.warnings_document(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return capture_path, sidecar
