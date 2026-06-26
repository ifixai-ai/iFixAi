import logging
import os
import sys

import click

from ifixai._version import VERSION
from ifixai.cli.init import init, load_dotenv_file
from ifixai.cli.run import run
from ifixai.cli.setup_cmd import setup
from ifixai.cli.list_cmd import list_group
from ifixai.cli.validate import validate
from ifixai.cli.compare import compare


@click.group()
@click.version_option(version=VERSION, prog_name="ifixai")
def ifixai_cli() -> None:
    pass


ifixai_cli.add_command(setup)
ifixai_cli.add_command(init)
ifixai_cli.add_command(run)
ifixai_cli.add_command(list_group, name="list")
ifixai_cli.add_command(validate)
ifixai_cli.add_command(compare)


def _ensure_utf8_stdout() -> None:
    if sys.platform != "win32":
        return
    import io

    def _fix(stream):  # noqa: E306
        enc = ""
        if hasattr(stream, "encoding") and stream.encoding:
            enc = stream.encoding
        if enc.lower().replace("-", "") == "utf8":
            return stream
        buf = stream.buffer if hasattr(stream, "buffer") else None
        if buf is None:
            return stream
        return io.TextIOWrapper(buf, encoding="utf-8", errors="replace")

    sys.stdout = _fix(sys.stdout)
    sys.stderr = _fix(sys.stderr)


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
    _ensure_utf8_stdout()
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
