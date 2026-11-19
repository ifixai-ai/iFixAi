"""The free Pro audit offer: a choice at the start of a run, and the steps at its end.

The start screen shows once per machine, before anything is sent to an agent, as
a choice between a free Pro audit and carrying on with open source. Picking Pro
ends the run, prints the steps, and offers to add the connector to Claude Code
when its CLI is installed. The steps screen is also the last thing printed on
every finished real run (not the mock agent, at least one inspection with a
verdict). Neither shows in CI or without a terminal. Skip both with
``--no-promo`` or ``IFIXAI_NO_PROMO=1``.
"""

import shutil

# Runs the user's own `claude` CLI with a fixed argument list, never a shell.
import subprocess  # nosec B404
import sys
import textwrap
from typing import Literal, TextIO, TypedDict

import click

from ifixai import telemetry
from ifixai.cli import ui
from ifixai.cli._branding import _ACCENT_RGB, supports_color
from ifixai.core.types import TestRunResult, TestStatus

NO_PROMO_ENV_VAR = "IFIXAI_NO_PROMO"
OFFER_MARKER_NAME = "pro-offer-shown"
MOCK_PROVIDER = "mock"
CONNECTOR_URL = "https://mcp.ifixai.ai/mcp"
PROMO_CODE = "SF2026"
CLAUDE_MCP_ADD_ARGS = (
    "mcp", "add", "--transport", "http", "--scope", "user", "ifixai", CONNECTOR_URL,
)
CLAUDE_ADD_TIMEOUT_SECONDS = 60
MIN_WRAP_WIDTH = 20

OfferPlace = Literal["start", "end"]
# (first-line indent, continuation indent, text): one paragraph, wrapped to the terminal.
Paragraph = tuple[str, str, str]

START_HEADLINE = (
    "Even when your AI agent does exactly what you ask,",
    "what else does it do along the way?",
)
START_BODY: tuple[Paragraph, ...] = (
    ("", "", "Try a complimentary iFixAi Pro audit through MCP."),
    ("", "", "We run 12 inspections. You get two reports showing what happened during "
             "testing, what it means for your business, and how the findings relate to "
             "relevant regulations."),
    ("", "", "No payment or subscription required."),
)
STEPS_HEADLINE = ("Run your free iFixAi Pro audit",)
STEPS_BODY: tuple[Paragraph, ...] = (
    ("1. ", "   ", "Add this remote MCP server in your coding agent."),
    ("   ", "   ", CONNECTOR_URL),
    ("2. ", "   ", "Sign in with Google or Microsoft."),
    ("3. ", "   ", 'In your coding agent, type "Audit my agent with iFixAi."'),
    ("   ", "   ", f"When prompted, redeem code {PROMO_CODE}."),
    ("", "", "The code covers 12 inspections, all findings, and both reports."),
    ("", "", "No payment or subscription required."),
)

PRO_OPTION = "Continue with a free Pro audit"
OPEN_SOURCE_OPTION = "Continue with open source"
ADD_FOR_ME_OPTION = "Add it to Claude Code for me"
ADD_MYSELF_OPTION = "I'll add it myself"


class ChoiceQuestion(TypedDict):
    """One question with a fixed set of answers; Enter alone picks ``default``."""

    message: str
    options: tuple[str, ...]
    default: str


START_QUESTION = ChoiceQuestion(
    message="How would you like to continue?",
    options=(PRO_OPTION, OPEN_SOURCE_OPTION),
    default=PRO_OPTION,
)
CLAUDE_CODE_QUESTION = ChoiceQuestion(
    # Short enough that the menu's echoed answer fits an 80-column line.
    message="Add the iFixAi server to Claude Code now?",
    options=(ADD_FOR_ME_OPTION, ADD_MYSELF_OPTION),
    default=ADD_FOR_ME_OPTION,
)


def offer_at_start(provider: str) -> bool:
    """Make the start offer once per machine; True when the user picked the Pro audit.

    A Pro pick has printed the steps (and offered the Claude Code setup), and the
    caller ends the run before anything is sent to an agent.
    """
    if provider.lower() == MOCK_PROVIDER or not can_ask_user():
        return False
    if not mark_offer_shown():
        return False
    click.echo()
    click.echo(render_screen(START_HEADLINE, START_BODY, sys.stdout))
    telemetry.emit_offer_shown("start")
    if ask_choice(START_QUESTION) != PRO_OPTION:
        return False
    telemetry.emit_offer_accepted("start")
    click.echo()
    click.echo(render_screen(STEPS_HEADLINE, STEPS_BODY, sys.stdout))
    offer_claude_code_setup()
    return True


def offer_at_end(provider: str, result: TestRunResult) -> None:
    """Print the steps after a finished real run that reached at least one verdict."""
    if provider.lower() == MOCK_PROVIDER or not person_at_terminal():
        return
    if not has_verdict(result):
        return
    click.echo(err=True)
    click.echo(render_screen(STEPS_HEADLINE, STEPS_BODY, sys.stderr), err=True)
    telemetry.emit_offer_shown("end")


def offer_claude_code_setup() -> None:
    """When Claude Code's CLI is on the PATH, offer to add the connector to it."""
    claude = shutil.which("claude")
    if claude is None:
        return
    click.echo()
    if ask_choice(CLAUDE_CODE_QUESTION) != ADD_FOR_ME_OPTION:
        return
    if add_connector_to_claude_code(claude):
        click.echo(click.style("Added the iFixAi server to Claude Code.", fg="green"))
    else:
        click.echo(click.style("Could not add it. Run this yourself:", fg="yellow"))
        click.echo(f"  claude {' '.join(CLAUDE_MCP_ADD_ARGS)}")
    click.echo("Restart Claude Code, then do steps 2 and 3.")


def add_connector_to_claude_code(claude: str) -> bool:
    """Run ``claude mcp add`` for the connector; True when it is there afterwards.

    An existing ``ifixai`` server counts as done: Claude Code refuses to add it twice.
    """
    try:
        # The argument list is fixed and `claude` is the user's own CLI from their PATH.
        completed = subprocess.run(  # nosec B603
            [claude, *CLAUDE_MCP_ADD_ARGS],
            capture_output=True,
            text=True,
            timeout=CLAUDE_ADD_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    answer = f"{completed.stdout}\n{completed.stderr}".lower()
    return completed.returncode == 0 or "already exists" in answer


def render_screen(
    headline: tuple[str, ...], body: tuple[Paragraph, ...], stream: TextIO | None
) -> str:
    """The screen as text, wrapped at word boundaries to the terminal, never mid-word.

    The URL and the code are single words, so they are never broken; they are
    coloured only after wrapping, so colour codes never count toward a line's width.
    """
    width = max(shutil.get_terminal_size().columns - 1, MIN_WRAP_WIDTH)
    colour = stream is not None and supports_color(stream)
    lines = [
        click.style(line, bold=True) if colour else line
        for text in headline
        for line in wrap(("", "", text), width)
    ]
    for paragraph in body:
        for line in wrap(paragraph, width):
            if colour:
                line = line.replace(CONNECTOR_URL, click.style(CONNECTOR_URL, fg="cyan"))
                line = line.replace(
                    PROMO_CODE, click.style(PROMO_CODE, fg=_ACCENT_RGB, bold=True)
                )
            lines.append(line)
    return "\n".join(lines)


def wrap(paragraph: Paragraph, width: int) -> list[str]:
    first_indent, rest_indent, text = paragraph
    return textwrap.wrap(
        text,
        width=width,
        initial_indent=first_indent,
        subsequent_indent=rest_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def has_verdict(result: TestRunResult) -> bool:
    """True when at least one inspection passed or failed."""
    return any(
        test.status in (TestStatus.PASS, TestStatus.FAIL)
        for test in result.test_results
    )


def can_ask_user() -> bool:
    """True only when a person is at a terminal to answer and read every stream, outside CI."""
    return (
        is_terminal(sys.stdin)
        and is_terminal(sys.stdout)
        and is_terminal(sys.stderr)
        and not telemetry.in_ci()
    )


def person_at_terminal() -> bool:
    """True only when a person reads both output streams at a terminal, outside CI."""
    return (
        is_terminal(sys.stdout) and is_terminal(sys.stderr) and not telemetry.in_ci()
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
