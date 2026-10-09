"""Pro-version offer shown at the start of an interactive ``ifixai run``.

The offer is made once per machine, before telemetry and before any provider
call, so a run the user abandons for the website leaves no trace beyond the
``pro-offer-shown`` marker. It is never shown when stdin or stdout is not a
terminal, or in CI, so scripted and agent-driven runs cannot block on it. Skip
it with ``--no-promo`` or ``IFIXAI_NO_PROMO=1``.
"""

import sys
import webbrowser
from enum import Enum
from typing import TextIO, TypedDict

import click

from ifixai import telemetry
from ifixai.cli import ui

PRO_URL = "https://www.ifixai.ai/"
NO_PROMO_ENV_VAR = "IFIXAI_NO_PROMO"
OFFER_MARKER_NAME = "pro-offer-shown"

YES_OPTION = "Yes"
NO_OPTION = "No"
CONTINUE_OPTION = "Continue the current run"
ABANDON_OPTION = "Abandon the run"


class RunDecision(str, Enum):
    """What the user wants done with the current run after seeing the offer."""

    CONTINUE = "continue"
    ABANDON = "abandon"


class ChoiceQuestion(TypedDict):
    """One question with a fixed set of answers; Enter alone picks ``default``."""

    message: str
    options: tuple[str, ...]
    default: str


PRO_INTEREST_QUESTION = ChoiceQuestion(
    message="Claim your free fast audit now?",
    options=(YES_OPTION, NO_OPTION),
    default=NO_OPTION,
)
RUN_DECISION_QUESTION = ChoiceQuestion(
    message="What would you like to do with this run?",
    options=(CONTINUE_OPTION, ABANDON_OPTION),
    default=CONTINUE_OPTION,
)


def offer_pro_version() -> RunDecision:
    """Offer the Pro version and report whether the current run should go ahead.

    Opens the Pro website when the user accepts. Returns ``CONTINUE`` without
    asking anything when nobody is at a terminal to answer or it was already shown.
    """
    if not can_prompt_user() or not mark_offer_shown():
        return RunDecision.CONTINUE
    print_offer()
    if ask_choice(PRO_INTEREST_QUESTION) != YES_OPTION:
        return RunDecision.CONTINUE
    open_pro_site()
    if ask_choice(RUN_DECISION_QUESTION) == ABANDON_OPTION:
        return RunDecision.ABANDON
    return RunDecision.CONTINUE


def can_prompt_user() -> bool:
    """True only when a person is at a terminal to answer the offer."""
    return (
        is_terminal(sys.stdin) and is_terminal(sys.stdout) and not telemetry.in_ci()
    )


def mark_offer_shown() -> bool:
    """Create the shown-once marker; False if it exists or can't be written.

    Marked before asking, so any answer (Ctrl-C included) counts. When it can't
    be written the offer is skipped, so a looping script never stops on it twice.
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


def print_offer() -> None:
    """Print the Pro-version pitch and its link."""
    click.echo(click.style("  iFixAi Pro", bold=True))
    click.echo("  Claim your free fast audit")
    click.echo(click.style(f"  {PRO_URL}", fg="cyan"))
    click.echo()


def open_pro_site() -> None:
    """Open the Pro website in the default browser; print the link when that fails."""
    if webbrowser.open(PRO_URL):
        click.echo(click.style(f"Opened {PRO_URL} in your browser.", fg="green"))
        return
    click.echo(
        click.style(
            f"Could not open a browser. Visit {PRO_URL} to claim your free fast audit.",
            fg="yellow",
        )
    )


def ask_choice(question: ChoiceQuestion) -> str:
    """Ask with an arrow-key menu, or a numbered list where menus are unavailable."""
    if ui.is_interactive():
        return ui.select_or_abort(
            question["message"], question["options"], question["default"]
        )
    return ask_numbered_choice(question)


def ask_numbered_choice(question: ChoiceQuestion) -> str:
    """List the options with numbers and read the number the user types.

    The plain path for terminals where the arrow-key menu cannot run: ``NO_COLOR``
    is set, or ``questionary`` is not installed.
    """
    options = question["options"]
    for number, option in enumerate(options, start=1):
        click.echo(f"  [{number}] {option}")
    typed_number = click.prompt(
        question["message"],
        type=click.IntRange(1, len(options)),
        default=options.index(question["default"]) + 1,
    )
    return options[typed_number - 1]
