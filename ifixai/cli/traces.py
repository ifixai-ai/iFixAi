"""The `ifixai traces` commands: import vendor trace exports as canonical captures."""

from __future__ import annotations

from pathlib import Path

import click

from ifixai.trace_intake import SOURCES, TraceIntakeError, import_trace, write_result
from ifixai.trace_intake._core import DEFAULT_MAX_TEXT, MAX_LABEL_CHARS


@click.group()
def traces() -> None:
    """Import traces your team already collects into canonical trajectory captures."""


@traces.command("import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--from", "source", type=click.Choice(["auto", *SOURCES]), default="auto",
    show_default=True, help="Trace format; `auto` recognizes it by structure.",
)
@click.option("--tenant", "tenant_id", required=True, help="Tenant id written into the capture.")
@click.option(
    "--collector", "collector_id", required=True, help="Collector id written into the capture.",
)
@click.option("--trace-id", default=None, help="Trace to import when the file holds several.")
@click.option(
    "--max-text", default=DEFAULT_MAX_TEXT, show_default=True,
    type=click.IntRange(0, MAX_LABEL_CHARS),
    help="Characters of generation text kept as an output label.",
)
@click.option(
    "--output", "-o", "output", required=True, type=click.Path(dir_okay=False, path_type=Path),
    help="Capture JSONL to write; warnings go to <output>.warnings.json.",
)
def import_command(
    file: Path, source: str, tenant_id: str, collector_id: str, trace_id: str | None,
    max_text: int, output: Path,
) -> None:
    """Map FILE to a canonical JSONL capture. Nothing in FILE is executed or fetched."""
    try:
        result = import_trace(
            file, source, tenant_id=tenant_id, collector_id=collector_id,
            trace_id=trace_id, max_text=max_text,
        )
        capture_path, sidecar = write_result(result, output)
    except (TraceIntakeError, OSError) as error:
        raise click.ClickException(str(error)) from error
    complete = "complete" if result.header["capture_complete"] else "incomplete"
    click.echo(
        f"Imported {len(result.events)} events from {result.source} into {capture_path} "
        f"({complete}); {len(result.warnings)} warning(s) in {sidecar}."
    )
    for warning in result.warnings[:20]:
        click.echo(f"  warning: {warning}", err=True)
