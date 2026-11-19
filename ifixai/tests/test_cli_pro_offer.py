"""The Pro-audit offer shown at the start of an interactive ``ifixai run``."""

import io
import sys
from collections.abc import Sequence
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import ifixai.cli.run as run_module
from ifixai import telemetry
from ifixai.cli import pro_offer, ui
from ifixai.cli.main import ifixai_cli
from ifixai.cli.pro_offer import (
    CODE_COVERAGE_NOTE,
    CONTINUE_PATH_QUESTION,
    COPY_INDENT,
    NO_PROMO_ENV_VAR,
    OFFER_HEADLINE_LINES,
    OFFER_PARAGRAPHS,
    OPEN_SOURCE_OPTION,
    PRO_AUDIT_OPTION,
    RunDecision,
    wrap_to_copy_width,
)

# Spelled out rather than imported: a mistyped server address or code would
# leave the user with steps that cannot be followed.
MCP_SERVER_URL = "https://mcp.ifixai.ai/mcp"
PROMO_CODE = "SF2026"
UNKNOWN_TEST_ID = "ZZ99"
UNKNOWN_TEST_ERROR = "unknown test ID"
STOPPED_MESSAGE = "No inspections were run"
TERMINAL_WIDTH = 80
OFFER_FIRST_LINE = f"{COPY_INDENT}{OFFER_HEADLINE_LINES[0]}\n"
OFFER_TEXT = (
    *(f"{COPY_INDENT}{line}\n" for line in OFFER_HEADLINE_LINES),
    *(wrap_to_copy_width(paragraph) for paragraph in OFFER_PARAGRAPHS),
)
SETUP_STEPS = (
    "  1. Add this remote MCP server in your coding agent.\n",
    f"     {MCP_SERVER_URL}\n",
    "  2. Sign in with Google or Microsoft.\n",
    '  3. In your coding agent, type "Audit my agent with iFixAi".\n',
    f"     When prompted, redeem code {PROMO_CODE}.\n",
    f"{COPY_INDENT}{CODE_COVERAGE_NOTE}\n",
)
FIRST_SETUP_STEP = SETUP_STEPS[0]
PREVIOUS_MARKER_NAME = "pro-offer-shown"
SKIPPED_RUNS = [
    (["--no-promo"], {}),
    ([], {NO_PROMO_ENV_VAR: "1"}),
    (["--dry-run"], {}),
]


class FakeStream(io.StringIO):
    """A text stream that reports whether it is attached to a terminal.

    ``typed_text`` is what a user would have typed, for a stream used as stdin.
    """

    def __init__(self, is_terminal: bool, typed_text: str = "") -> None:
        super().__init__(typed_text)
        self.is_terminal = is_terminal

    def isatty(self) -> bool:
        return self.is_terminal


class OfferRecorder:
    """Stands in for ``offer_pro_version`` and counts how often the run asked."""

    def __init__(self, decision: RunDecision) -> None:
        self.decision = decision
        self.call_count = 0

    def __call__(self) -> RunDecision:
        self.call_count += 1
        return self.decision


class CallCounter:
    """Stands in for a no-argument side effect and counts its calls."""

    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self) -> None:
        self.call_count += 1


class ScriptedMenu:
    """Stands in for the arrow-key menu and answers each question in order."""

    def __init__(self, answers: list[str]) -> None:
        self.remaining_answers = iter(answers)
        self.offered_choices: list[tuple[str, ...]] = []
        self.defaults: list[str] = []

    def __call__(self, message: str, choices: Sequence[str], default: str) -> str:
        self.offered_choices.append(tuple(choices))
        self.defaults.append(default)
        return next(self.remaining_answers)


class CancelledMenu:
    """Stands in for the arrow-key menu when the user presses Ctrl-C on it."""

    def __call__(self, message: str, choices: Sequence[str], default: str) -> str:
        raise KeyboardInterrupt


class UnexpectedMenu:
    """Stands in for the arrow-key menu on a terminal where it must not be used."""

    def __call__(self, message: str, choices: Sequence[str], default: str) -> str:
        raise AssertionError("the arrow-key menu was used where it cannot run")


def report_terminal_attached() -> bool:
    return True


def report_outside_ci() -> bool:
    return False


def find_missing_copy(output: str, expected_copy: Sequence[str]) -> list[str]:
    """The expected lines that do not appear in ``output``, in their listed order."""
    return [line for line in expected_copy if line not in output]


def is_in_listed_order(output: str, expected_copy: Sequence[str]) -> bool:
    """True when every expected line appears in ``output`` in the order listed."""
    positions = [output.index(line) for line in expected_copy]
    return positions == sorted(positions)


@click.command()
def show_offer_decision() -> None:
    """Minimal host command so CliRunner can answer the offer's prompts."""
    click.echo(f"decision={pro_offer.offer_pro_version().value}")


@pytest.fixture(autouse=True)
def offer_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the shown-once marker lands; kept in ``tmp_path``, never the real home."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "ifixai" / "pro-audit-offer-shown"


@pytest.fixture
def attached_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pro_offer, "can_prompt_user", report_terminal_attached)


@pytest.fixture
def run_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A ``run`` invocation that stops at validation, before any inspection."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(NO_PROMO_ENV_VAR, raising=False)
    return [
        "run",
        "--provider",
        "mock",
        "--eval-mode",
        "self",
        "--test",
        UNKNOWN_TEST_ID,
        "--no-telemetry",
    ]


@pytest.fixture
def mock_run_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A ``run`` invocation that executes one offline inspection into ``tmp_path``."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(NO_PROMO_ENV_VAR, raising=False)
    return [
        "run",
        "--provider",
        "mock",
        "--fixture",
        "customer_support",
        "--test",
        "B01",
        "--eval-mode",
        "self",
        "--no-telemetry",
        "--no-parallel",
        "--output",
        str(tmp_path / "reports"),
        "--reliability-out",
        str(tmp_path / "runs"),
    ]


@pytest.mark.parametrize("answers", ["2\n", "\n"])
def test_choosing_open_source_continues_the_run_without_the_setup_steps(
    attached_terminal: None, answers: str
) -> None:
    result = CliRunner().invoke(show_offer_decision, input=answers)

    assert result.exit_code == 0, result.output
    assert OFFER_FIRST_LINE in result.output
    assert "decision=continue" in result.output
    assert FIRST_SETUP_STEP not in result.output
    assert MCP_SERVER_URL not in result.output


def test_choosing_the_pro_audit_shows_the_steps_in_order_and_stops_the_run(
    attached_terminal: None,
) -> None:
    result = CliRunner().invoke(show_offer_decision, input="1\n")

    assert result.exit_code == 0, result.output
    assert find_missing_copy(result.output, SETUP_STEPS) == []
    assert is_in_listed_order(result.output, SETUP_STEPS)
    assert "decision=abandon" in result.output


def test_the_offer_text_is_printed_in_full_and_in_order(
    attached_terminal: None,
) -> None:
    result = CliRunner().invoke(show_offer_decision, input="2\n")

    assert find_missing_copy(result.output, OFFER_TEXT) == []
    assert is_in_listed_order(result.output, OFFER_TEXT)


@pytest.mark.parametrize("answer", ["1\n", "2\n"])
def test_every_line_on_screen_fits_an_80_column_terminal(
    attached_terminal: None, answer: str
) -> None:
    result = CliRunner().invoke(show_offer_decision, input=answer)

    lines = result.output.splitlines()
    assert [line for line in lines if len(line) > TERMINAL_WIDTH] == []


def test_the_offer_is_printed_before_the_steps(attached_terminal: None) -> None:
    result = CliRunner().invoke(show_offer_decision, input="1\n")

    output = result.output
    assert output.index(OFFER_FIRST_LINE) < output.index(FIRST_SETUP_STEP)


def test_a_number_outside_the_list_is_refused_and_asked_again(
    attached_terminal: None,
) -> None:
    result = CliRunner().invoke(show_offer_decision, input="7\n0\nyes\n1\n")

    assert result.exit_code == 0, result.output
    assert result.output.count("Error:") == 3
    assert "decision=abandon" in result.output


def test_no_color_terminals_get_the_numbered_list_not_the_arrow_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(sys, "stdin", FakeStream(is_terminal=True, typed_text="1\n"))
    monkeypatch.setattr(sys, "stdout", FakeStream(is_terminal=True))
    monkeypatch.setattr(ui, "select_or_abort", UnexpectedMenu())

    assert ui.is_interactive() is False
    assert pro_offer.ask_choice(CONTINUE_PATH_QUESTION) == PRO_AUDIT_OPTION


def test_no_terminal_means_no_prompt_and_the_run_continues(offer_marker: Path) -> None:
    # CliRunner's streams are not terminals, so the real gate is exercised here.
    result = CliRunner().invoke(show_offer_decision, input="1\n")

    assert result.output.strip() == "decision=continue"
    assert not offer_marker.exists()


@pytest.mark.parametrize(
    "is_stdin_terminal,is_stdout_terminal,expected",
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_prompting_needs_a_terminal_on_both_streams(
    monkeypatch: pytest.MonkeyPatch,
    is_stdin_terminal: bool,
    is_stdout_terminal: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)
    monkeypatch.setattr(sys, "stdin", FakeStream(is_stdin_terminal))
    monkeypatch.setattr(sys, "stdout", FakeStream(is_stdout_terminal))

    assert pro_offer.can_prompt_user() is expected


@pytest.mark.parametrize("missing_stream", ["stdin", "stdout"])
def test_a_missing_standard_stream_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch, missing_stream: str
) -> None:
    # Python sets a standard stream to None when the process has no handle for
    # it, e.g. `ifixai run ... <&-` or a detached Windows process.
    monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)
    monkeypatch.setattr(sys, "stdin", FakeStream(is_terminal=True))
    monkeypatch.setattr(sys, "stdout", FakeStream(is_terminal=True))
    monkeypatch.setattr(sys, missing_stream, None)

    assert pro_offer.can_prompt_user() is False


def test_ci_is_never_prompted_even_with_a_terminal(
    monkeypatch: pytest.MonkeyPatch, offer_marker: Path
) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(sys, "stdin", FakeStream(is_terminal=True))
    monkeypatch.setattr(sys, "stdout", FakeStream(is_terminal=True))

    assert pro_offer.can_prompt_user() is False
    assert pro_offer.offer_pro_version() is RunDecision.CONTINUE
    assert not offer_marker.exists()


@pytest.mark.parametrize("extra_args,env", SKIPPED_RUNS)
def test_run_skips_the_offer_when_told_to_or_when_nothing_will_run(
    run_args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    env: dict[str, str],
) -> None:
    offer = OfferRecorder(RunDecision.ABANDON)
    monkeypatch.setattr(run_module, "offer_pro_version", offer)

    result = CliRunner().invoke(ifixai_cli, [*run_args, *extra_args], env=env)

    assert offer.call_count == 0
    assert STOPPED_MESSAGE not in result.output
    assert UNKNOWN_TEST_ERROR in result.output


def test_run_makes_the_offer_once_and_continues_when_the_user_keeps_the_run(
    run_args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    offer = OfferRecorder(RunDecision.CONTINUE)
    monkeypatch.setattr(run_module, "offer_pro_version", offer)

    result = CliRunner().invoke(ifixai_cli, run_args)

    assert offer.call_count == 1
    assert result.exit_code == 1, result.output
    assert UNKNOWN_TEST_ERROR in result.output


def test_choosing_the_pro_audit_stops_before_the_run_leaves_any_trace(
    tmp_path: Path,
    mock_run_args: list[str],
    attached_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = CallCounter()
    monkeypatch.setattr(telemetry, "emit_started", started)

    result = CliRunner().invoke(ifixai_cli, mock_run_args, input="1\n")

    assert result.exit_code == 0, result.output
    assert MCP_SERVER_URL in result.output
    assert STOPPED_MESSAGE in result.output
    assert started.call_count == 0
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "reports").exists()


def test_choosing_open_source_runs_the_inspections(
    tmp_path: Path,
    mock_run_args: list[str],
    attached_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = CallCounter()
    monkeypatch.setattr(telemetry, "emit_started", started)

    result = CliRunner().invoke(ifixai_cli, mock_run_args, input="2\n")

    assert STOPPED_MESSAGE not in result.output
    assert started.call_count == 1
    assert (tmp_path / "runs").exists()
    assert list((tmp_path / "reports").glob("*.json")), result.output


def test_input_ending_at_the_numbered_prompt_aborts_instead_of_starting_the_run(
    tmp_path: Path,
    mock_run_args: list[str],
    attached_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = CallCounter()
    monkeypatch.setattr(telemetry, "emit_started", started)

    result = CliRunner().invoke(ifixai_cli, mock_run_args, input="")

    assert result.exit_code == 1, result.output
    assert "Aborted!" in result.output
    assert started.call_count == 0
    assert not (tmp_path / "runs").exists()


def test_choosing_open_source_at_the_real_prompt_lets_the_run_proceed(
    run_args: list[str], attached_terminal: None
) -> None:
    result = CliRunner().invoke(ifixai_cli, run_args, input="2\n")

    assert result.exit_code == 1, result.output
    assert OFFER_FIRST_LINE in result.output
    assert UNKNOWN_TEST_ERROR in result.output
    assert FIRST_SETUP_STEP not in result.output


@pytest.mark.parametrize(
    "answer,expected",
    [
        (OPEN_SOURCE_OPTION, RunDecision.CONTINUE),
        (PRO_AUDIT_OPTION, RunDecision.ABANDON),
    ],
)
def test_arrow_menu_answers_drive_the_same_decisions(
    attached_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    expected: RunDecision,
) -> None:
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    monkeypatch.setattr(ui, "select_or_abort", ScriptedMenu([answer]))

    assert pro_offer.offer_pro_version() is expected


def test_the_menu_lists_the_pro_audit_first_and_starts_on_open_source(
    attached_terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    menu = ScriptedMenu([OPEN_SOURCE_OPTION])
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    monkeypatch.setattr(ui, "select_or_abort", menu)

    pro_offer.offer_pro_version()

    assert menu.offered_choices == [(PRO_AUDIT_OPTION, OPEN_SOURCE_OPTION)]
    assert menu.defaults == [OPEN_SOURCE_OPTION]


def test_ctrl_c_on_the_arrow_menu_aborts_instead_of_starting_the_run(
    tmp_path: Path,
    mock_run_args: list[str],
    attached_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = CallCounter()
    monkeypatch.setattr(telemetry, "emit_started", started)
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    monkeypatch.setattr(ui, "select_or_abort", CancelledMenu())

    result = CliRunner().invoke(ifixai_cli, mock_run_args)

    assert result.exit_code == 1, result.output
    assert "Aborted!" in result.output
    assert started.call_count == 0
    assert not (tmp_path / "runs").exists()


def test_a_second_run_starts_straight_away_without_the_offer(
    run_args: list[str], attached_terminal: None, offer_marker: Path
) -> None:
    first = CliRunner().invoke(ifixai_cli, run_args, input="2\n")
    # No input: a second offer would stop at the prompt and abort.
    second = CliRunner().invoke(ifixai_cli, run_args, input="")

    assert OFFER_FIRST_LINE in first.output
    assert offer_marker.exists()
    assert OFFER_FIRST_LINE not in second.output
    assert "Aborted!" not in second.output
    assert UNKNOWN_TEST_ERROR in second.output


def test_the_previous_marker_name_does_not_hide_the_offer(
    attached_terminal: None, offer_marker: Path
) -> None:
    previous_marker = offer_marker.with_name(PREVIOUS_MARKER_NAME)
    previous_marker.parent.mkdir(parents=True)
    previous_marker.touch()

    first = CliRunner().invoke(show_offer_decision, input="2\n")
    second = CliRunner().invoke(show_offer_decision, input="")

    assert OFFER_FIRST_LINE in first.output
    assert offer_marker.exists()
    assert second.output.strip() == "decision=continue"


@pytest.mark.parametrize("answer", [OPEN_SOURCE_OPTION, PRO_AUDIT_OPTION])
def test_any_answer_counts_as_shown(
    attached_terminal: None, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    monkeypatch.setattr(ui, "select_or_abort", ScriptedMenu([answer]))
    pro_offer.offer_pro_version()
    monkeypatch.setattr(ui, "select_or_abort", UnexpectedMenu())

    assert pro_offer.offer_pro_version() is RunDecision.CONTINUE


def test_ctrl_c_on_the_offer_counts_as_shown(
    attached_terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    monkeypatch.setattr(ui, "select_or_abort", CancelledMenu())
    with pytest.raises(KeyboardInterrupt):
        pro_offer.offer_pro_version()
    monkeypatch.setattr(ui, "select_or_abort", UnexpectedMenu())

    assert pro_offer.offer_pro_version() is RunDecision.CONTINUE


@pytest.mark.parametrize("extra_args,env", SKIPPED_RUNS)
def test_a_skipped_offer_is_not_marked_shown(
    run_args: list[str],
    attached_terminal: None,
    offer_marker: Path,
    extra_args: list[str],
    env: dict[str, str],
) -> None:
    result = CliRunner().invoke(ifixai_cli, [*run_args, *extra_args], env=env)

    assert OFFER_FIRST_LINE not in result.output
    assert UNKNOWN_TEST_ERROR in result.output
    assert not offer_marker.exists()


@pytest.mark.parametrize("blocking_file", ["config", "config/ifixai"])
def test_a_marker_that_cannot_be_written_skips_the_offer_and_runs(
    tmp_path: Path,
    run_args: list[str],
    attached_terminal: None,
    blocking_file: str,
) -> None:
    # A file where the config dir should be: the marker can't be created.
    blocker = tmp_path / blocking_file
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory", encoding="utf-8")

    result = CliRunner().invoke(ifixai_cli, run_args, input="")

    assert OFFER_FIRST_LINE not in result.output
    assert "Aborted!" not in result.output
    assert UNKNOWN_TEST_ERROR in result.output
