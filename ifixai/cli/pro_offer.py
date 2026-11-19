"""The hosted-audit offer printed at the start and at the end of an ``ifixai run``.

Three lines on stderr, never a question: the run never waits on them. The start
lines show once per machine; the end lines show after every finished real run
(not the mock agent, at least one inspection with a verdict). Both need a person
at a terminal: never when stdout or stderr is not a terminal, or in CI. Skip them
with ``--no-promo`` or ``IFIXAI_NO_PROMO=1``.
"""

import sys
from typing import Literal, TextIO

import click

from ifixai import telemetry
from ifixai.core.types import TestRunResult, TestStatus

NO_PROMO_ENV_VAR = "IFIXAI_NO_PROMO"
OFFER_MARKER_NAME = "pro-offer-shown"
MOCK_PROVIDER = "mock"

OfferPlace = Literal["start", "end"]

OFFER_HEADLINE = (
    "One free hosted audit for you: up to 12 inspections the open-source set does "
    "not have, 20 minutes at most."
)
OFFER_COMMAND_LINE = (
    "Add iFixAi to your coding agent: claude mcp add --transport http --scope user "
    "ifixai https://mcp.ifixai.ai/mcp"
)
OFFER_SAY_LINE = "Then say: audit my agent with code MYAUDIT"


def offer_at_start(provider: str) -> None:
    """Print the offer once per machine, before the first real run's inspections."""
    if provider.lower() == MOCK_PROVIDER or not person_at_terminal():
        return
    if not mark_offer_shown():
        return
    print_offer("start")


def offer_at_end(provider: str, result: TestRunResult) -> None:
    """Print the offer after a finished real run that reached at least one verdict."""
    if provider.lower() == MOCK_PROVIDER or not person_at_terminal():
        return
    if not has_verdict(result):
        return
    print_offer("end")


def has_verdict(result: TestRunResult) -> bool:
    """True when at least one inspection passed or failed."""
    return any(
        test.status in (TestStatus.PASS, TestStatus.FAIL)
        for test in result.test_results
    )


def person_at_terminal() -> bool:
    """True only when a person reads both output streams at a terminal, outside CI."""
    return (
        is_terminal(sys.stdout) and is_terminal(sys.stderr) and not telemetry.in_ci()
    )


def mark_offer_shown() -> bool:
    """Create the shown-once marker; False if it exists or can't be written.

    When it can't be written the start lines are skipped, so a machine that
    can't keep the marker doesn't see them on every run.
    """
    try:
        (telemetry._ensure_config_dir() / OFFER_MARKER_NAME).touch(exist_ok=False)
    except OSError:
        return False
    return True


def is_terminal(stream: TextIO | None) -> bool:
    """True when the stream exists and is attached to a terminal.

    Python sets a standard stream to ``None`` when the process has no handle
    for it (a closed descriptor, ``pythonw``, a detached Windows process).
    """
    return stream is not None and stream.isatty()


def print_offer(where: OfferPlace) -> None:
    """Print the three offer lines on stderr and count that they were shown."""
    click.echo(err=True)
    click.echo(click.style(OFFER_HEADLINE, bold=True), err=True)
    click.echo(OFFER_COMMAND_LINE, err=True)
    click.echo(OFFER_SAY_LINE, err=True)
    click.echo(err=True)
    telemetry.emit_offer_shown(where)
