"""Show the human exactly what would be published.

The safety property this file exists for: a submitter sees the original and the redaction side by
side, line for line, before anything leaves the machine. Not a count of replacements, not a
summary — the actual text, because the thing a scrubber misses is by definition the thing a summary
would not mention.

Rendered with ``rich`` when a console is available, which the project already depends on, and in
plain text otherwise so a redirected run or a CI log still shows the same content.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import yaml

from ifixai.register.scrub import Finding, ScrubResult

REDACTED_MARK = "~"
UNCHANGED_MARK = " "


def _lines(payload: dict[str, Any]) -> list[str]:
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100).splitlines()


def aligned(result: ScrubResult) -> tuple[tuple[str, str, str], ...]:
    """``(mark, original_line, redacted_line)`` for every line of the report.

    The two payloads have the same shape — the scrubber replaces values and never adds or removes
    keys — so a positional walk is a true alignment and needs no diff algorithm. If that ever stops
    being true, the padding below keeps the render honest rather than silently dropping a line.
    """
    original = _lines(result.original_payload)
    redacted = _lines(result.redacted_payload)
    height = max(len(original), len(redacted))
    original += [""] * (height - len(original))
    redacted += [""] * (height - len(redacted))
    rows = []
    for left, right in zip(original, redacted):
        mark = UNCHANGED_MARK if left == right else REDACTED_MARK
        rows.append((mark, left, right))
    return tuple(rows)


def plain(result: ScrubResult, width: int = 56) -> str:
    """The side-by-side as plain text. Changed rows are marked, so a grep over a CI log finds them."""
    header = f"{'ORIGINAL (stays on your machine)':<{width}} | REDACTED (what would be published)"
    out = [header, "-" * (width + 3 + width)]
    for mark, left, right in aligned(result):
        out.append(f"{left[:width]:<{width}} {mark}| {right[:width]}")
    out.append("")
    out.append(summary(result.findings))
    return "\n".join(out)


def summary(findings: Sequence[Finding]) -> str:
    if not findings:
        return (
            "The de-identifier replaced nothing. Read the right-hand column anyway: it found nothing, which is "
            "not the same as there being nothing to find."
        )
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.kind] = counts.get(finding.kind, 0) + 1
    listed = ", ".join(f"{kind.lower()} x{count}" for kind, count in sorted(counts.items()))
    return f"Replaced {len(findings)} value(s): {listed}."


def render(result: ScrubResult) -> None:
    """Print the side-by-side, preferring a rich table."""
    try:
        from rich.console import Console
        from rich.table import Table
    except ImportError:  # pragma: no cover - rich is a hard dependency; this is belt and braces
        print(plain(result))
        return
    console = Console()
    table = Table(show_lines=False, expand=True)
    table.add_column("", width=1)
    table.add_column("ORIGINAL (stays on your machine)", overflow="fold")
    table.add_column("REDACTED (what would be published)", overflow="fold")
    for mark, left, right in aligned(result):
        style = "yellow" if mark == REDACTED_MARK else None
        table.add_row(mark, left, right, style=style)
    console.print(table)
    console.print(summary(result.findings))
