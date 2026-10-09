import os
import re
import stat
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import click

from ifixai.core.types import TestRunResult
from ifixai.reporting.scorecard import (
    generate_json_report,
    generate_markdown_report,
    generate_summary_report,
)


def _slugify(value: str) -> str:
    if not value:
        return "unknown"
    stem = Path(value).stem if ("/" in value or "\\" in value) else value
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower()
    return cleaned or "unknown"


def _write_report_atomic(path: Path, content: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    temporary = path.parent / f".ifixai-report-{uuid4().hex}.tmp"
    # New exports follow normal file-creation permissions, including the umask.
    # Replacements stay private until the existing target mode is restored.
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o666 if mode is None else 0o600,
    )
    os.close(descriptor)
    try:
        temporary.write_text(content, encoding="utf-8")
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def save_reports(
    result: TestRunResult,
    output_dir: str,
    report_format: str,
    run_nonce: str | None = None,
    *,
    run_id: str | None = None,
) -> None:
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    system_slug = _slugify(result.system_name)
    fixture_slug = _slugify(result.fixture_name)
    base_name = f"ifixai-{system_slug}-{fixture_slug}"
    # The manifest identity distinguishes configuration changes even with a
    # pinned nonce. Direct callers without a manifest retain the complete nonce.
    suffix = run_id or run_nonce
    if suffix:
        base_name = f"{base_name}-{_slugify(suffix)}"

    click.echo(click.style("Reports saved:", bold=True))

    if report_format in ("markdown", "both"):
        summary_path = out_path / f"{base_name}-summary.md"
        _write_report_atomic(summary_path, generate_summary_report(result))
        click.echo(f"  Summary (start here): {summary_path}")

        md_path = out_path / f"{base_name}.md"
        _write_report_atomic(md_path, generate_markdown_report(result))
        click.echo(f"  Full report:          {md_path}")

    if report_format in ("json", "both"):
        json_path = out_path / f"{base_name}.json"
        _write_report_atomic(json_path, generate_json_report(result))
        click.echo(f"  JSON (machine):       {json_path}")
