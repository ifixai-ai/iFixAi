"""Pro-version offer shown at the start of an interactive ``ifixai run``.

The offer is made once per machine, before telemetry and before any provider
call. Choosing the free Pro audit prints the steps that start it from a coding
agent and stops the run, so it leaves no trace beyond the
``pro-audit-offer-shown`` marker. It is never shown when stdin or stdout is not
a terminal, or in CI, so scripted and agent-driven runs cannot block on it.
Skip it with ``--no-promo`` or ``IFIXAI_NO_PROMO=1``.
"""

import sys
from enum import Enum
from typing import TextIO, TypedDict

import click

from ifixai import telemetry
from ifixai.cli import ui
from ifixai.cli._branding import ACCENT_RGB

MCP_SERVER_URL = "https://mcp.ifixai.ai/mcp"
PROMO_CODE = "SF2026"
FREE_AUDIT_INSPECTION_COUNT = 12
NO_PROMO_ENV_VAR = "IFIXAI_NO_PROMO"
# Renamed from "pro-offer-shown" when the offer changed, so a machine that saw
# the old offer is shown the new one once.
OFFER_MARKER_NAME = "pro-audit-offer-shown"

PRO_AUDIT_OPTION = "Continue with a free Pro audit"
OPEN_SOURCE_OPTION = "Continue with open source"

COPY_INDENT = "  "
# Narrow enough for an 80-column terminal once the indent is added.
COPY_WIDTH = 76
OFFER_HEADLINE_LINES = (
    "Even when your AI agent does exactly what you ask,",
    "what else does it do along the way?",
)
NO_PAYMENT_NOTE = "No payment or subscription required."
OFFER_PARAGRAPHS = (
    "Try a complimentary iFixAi Pro audit through MCP.",
    f"We run {FREE_AUDIT_INSPECTION_COUNT} inspections. You get two reports "
    "showing what happened during testing, what it means for your business, "
    "and how the findings relate to relevant regulations.",
    NO_PAYMENT_NOTE,
)
CODE_COVERAGE_NOTE = (
    f"The code covers {FREE_AUDIT_INSPECTION_COUNT} inspections, all findings, "
    "and both reports."
)


class RunDecision(str, Enum):
    """What the user wants done with the current run after seeing the offer."""

    CONTINUE = "continue"
    ABANDON = "abandon"


class ChoiceQuestion(TypedDict):
    """One question with a fixed set of answers; Enter alone picks ``default``."""

    message: str
    options: tuple[str, ...]
    default: str


# Open source is preselected so Enter alone keeps the run going.
CONTINUE_PATH_QUESTION = ChoiceQuestion(
    message="How would you like to continue?",
    options=(PRO_AUDIT_OPTION, OPEN_SOURCE_OPTION),
    default=OPEN_SOURCE_OPTION,
)


def offer_pro_version() -> RunDecision:
    """Offer the free Pro audit and report whether the current run should go ahead.

    Choosing the Pro audit prints how to start it and stops the run. Returns
    ``CONTINUE`` without asking anything when nobody is at a terminal to answer
    or the offer was already shown.
    """
    if not can_prompt_user() or not mark_offer_shown():
        return RunDecision.CONTINUE
    print_offer()
    if ask_choice(CONTINUE_PATH_QUESTION) != PRO_AUDIT_OPTION:
        return RunDecision.CONTINUE
    print_audit_steps()
    return RunDecision.ABANDON


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
    """Print the offer: the question the audit answers and what it covers."""
    for line in OFFER_HEADLINE_LINES:
        click.echo(click.style(f"{COPY_INDENT}{line}", bold=True))
    for paragraph in OFFER_PARAGRAPHS:
        click.echo()
        click.echo(wrap_to_copy_width(paragraph))
    click.echo()


def wrap_to_copy_width(paragraph: str) -> str:
    """Wrap one paragraph of offer copy to the indented copy width."""
    return click.wrap_text(
        paragraph,
        width=COPY_WIDTH,
        initial_indent=COPY_INDENT,
        subsequent_indent=COPY_INDENT,
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


def print_audit_steps() -> None:
    """Print the three steps that start the free Pro audit from a coding agent."""
    server_url = click.style(MCP_SERVER_URL, fg="cyan")
    promo_code = click.style(PROMO_CODE, fg=ACCENT_RGB, bold=True)
    click.echo()
    click.echo(click.style(f"{COPY_INDENT}Run your free iFixAi Pro audit", bold=True))
    click.echo()
    click.echo(f"{COPY_INDENT}1. Add this remote MCP server in your coding agent.")
    click.echo(f"{COPY_INDENT}   {server_url}")
    click.echo()
    click.echo(f"{COPY_INDENT}2. Sign in with Google or Microsoft.")
    click.echo()
    click.echo(
        f'{COPY_INDENT}3. In your coding agent, type "Audit my agent with iFixAi".'
    )
    click.echo(f"{COPY_INDENT}   When prompted, redeem code {promo_code}.")
    click.echo()
    click.echo(f"{COPY_INDENT}{CODE_COVERAGE_NOTE}")
    click.echo(f"{COPY_INDENT}{NO_PAYMENT_NOTE}")
    click.echo()
