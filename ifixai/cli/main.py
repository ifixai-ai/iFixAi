import io
import logging
import os
import sys

import click

from ifixai._version import VERSION
from ifixai.cli.audit import audit, findings, mcp
from ifixai.cli.author import author
from ifixai.cli.compare import compare
from ifixai.cli.init import init, load_dotenv_file
from ifixai.cli.list_cmd import list_group
from ifixai.cli.policy_lab import policy_lab
from ifixai.cli.report import report
from ifixai.cli.run import run
from ifixai.cli.scaffold import install
from ifixai.cli.setup_cmd import setup
from ifixai.cli.traces import traces
from ifixai.cli.trajectory import trajectory
from ifixai.cli.validate import validate
from ifixai.cli.windtunnel import windtunnel


@click.group()
@click.version_option(version=VERSION, prog_name="ifixai")
def ifixai_cli() -> None:
    pass


ifixai_cli.add_command(setup)
ifixai_cli.add_command(init)
ifixai_cli.add_command(install)
ifixai_cli.add_command(author)
ifixai_cli.add_command(run)
ifixai_cli.add_command(audit)
ifixai_cli.add_command(findings)
ifixai_cli.add_command(mcp)
ifixai_cli.add_command(list_group, name="list")
ifixai_cli.add_command(validate)
ifixai_cli.add_command(compare)
ifixai_cli.add_command(trajectory)
ifixai_cli.add_command(traces)
ifixai_cli.add_command(policy_lab)
ifixai_cli.add_command(windtunnel)
ifixai_cli.add_command(report)


def _ensure_printable_output() -> None:
    """Never crash on a character the console can't show. Windows pipes default to the ANSI code
    page, so there stdout and stderr switch to UTF-8; any other non-UTF-8 stream keeps its
    encoding and prints `?` for what it lacks, instead of losing the run at the scorecard."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding == "utf8" or not isinstance(stream, io.TextIOWrapper):
            continue
        if sys.platform == "win32":
            stream.reconfigure(encoding="utf-8", errors="replace")
        else:
            stream.reconfigure(errors="replace")


class _CleanFormatter(logging.Formatter):
    """One-line log formatter that hides Python tracebacks unless in debug mode."""

    def __init__(self, show_tracebacks: bool) -> None:
        super().__init__("%(message)s")
        self._show_tracebacks = show_tracebacks

    def format(self, record: logging.LogRecord) -> str:
        if not self._show_tracebacks and (record.exc_info or record.stack_info):
            record = logging.makeLogRecord(record.__dict__)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return super().format(record)


def _configure_cli_logging() -> None:
    """Install a clean console handler so runs aren't buried in tracebacks."""
    debug = bool(os.environ.get("IFIXAI_DEBUG") or os.environ.get("IFIXAI_VERBOSE"))
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_CleanFormatter(show_tracebacks=debug))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if debug else logging.WARNING)


def main() -> None:
    _ensure_printable_output()
    loaded = load_dotenv_file()
    _configure_cli_logging()
    if loaded:
        click.echo(
            click.style(
                f"Loaded {len(loaded)} key(s) from .env: {', '.join(loaded)}", dim=True
            ),
            err=True,
        )
    ifixai_cli()


if __name__ == "__main__":
    main()
