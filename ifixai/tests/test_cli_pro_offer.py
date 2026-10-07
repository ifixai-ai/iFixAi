"""The Pro-version offer shown at the start of an interactive ``ifixai run``."""

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
    ABANDON_OPTION,
    CONTINUE_OPTION,
    NO_OPTION,
    NO_PROMO_ENV_VAR,
    PRO_INTEREST_QUESTION,
    PRO_URL,
    YES_OPTION,
    RunDecision,
)

UNKNOWN_TEST_ID = "ZZ99"
UNKNOWN_TEST_ERROR = "unknown test ID"
ABANDONED_MESSAGE = "Run abandoned"


class FakeStream(io.StringIO):
    """A text stream that reports whether it is attached to a terminal.

    ``typed_text`` is what a user would have typed, for a stream used as stdin.
    """

    def __init__(self, is_terminal: bool, typed_text: str = "") -> None:
        super().__init__(typed_text)
        self.is_terminal = is_terminal

    def isatty(self) -> bool:
        return self.is_terminal


class BrowserRecorder:
    """Stands in for ``webbrowser.open`` and records the URLs it was asked for."""

    def __init__(self, is_launch_successful: bool) -> None:
        self.is_launch_successful = is_launch_successful
        self.opened_urls: list[str] = []

    def __call__(self, url: str) -> bool:
        self.opened_urls.append(url)
        return self.is_launch_successful


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
        self.defaults: list[str] = []

    def __call__(self, message: str, choices: Sequence[str], default: str) -> str:
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


@click.command()
def show_offer_decision() -> None:
    """Minimal host command so CliRunner can answer the offer's prompts."""
    click.echo(f"decision={pro_offer.offer_pro_version().value}")


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> BrowserRecorder:
    recorder = BrowserRecorder(is_launch_successful=True)
    monkeypatch.setattr(pro_offer.webbrowser, "open", recorder)
    return recorder


@pytest.fixture
def attached_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pro_offer, "can_prompt_user", report_terminal_attached)


@pytest.fixture
def run_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A ``run`` invocation that stops at validation, before any inspection."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(NO_PROMO_ENV_VAR, raising=False)
    return [
        "run", "--provider", "mock", "--eval-mode", "self",
        "--test", UNKNOWN_TEST_ID, "--no-telemetry",
    ]


@pytest.fixture
def mock_run_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A ``run`` invocation that executes one offline inspection into ``tmp_path``."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(NO_PROMO_ENV_VAR, raising=False)
    return [
        "run", "--provider", "mock", "--fixture", "customer_support",
        "--test", "B01", "--eval-mode", "self", "--no-telemetry", "--no-parallel",
        "--output", str(tmp_path / "reports"),
        "--reliability-out", str(tmp_path / "runs"),
    ]


@pytest.mark.parametrize("answers", ["2\n", "\n"])
def test_declining_continues_the_run_without_opening_the_site(
    attached_terminal: None, browser: BrowserRecorder, answers: str
) -> None:
    result = CliRunner().invoke(show_offer_decision, input=answers)

    assert result.exit_code == 0, result.output
    assert PRO_URL in result.output
    assert "decision=continue" in result.output
    assert browser.opened_urls == []


@pytest.mark.parametrize(
    "answers,expected",
    [("1\n1\n", "continue"), ("1\n\n", "continue"), ("1\n2\n", "abandon")],
)
def test_accepting_opens_the_site_then_asks_what_to_do_with_the_run(
    attached_terminal: None, browser: BrowserRecorder, answers: str, expected: str
) -> None:
    result = CliRunner().invoke(show_offer_decision, input=answers)

    assert result.exit_code == 0, result.output
    assert browser.opened_urls == [PRO_URL]
    assert "Continue the current run" in result.output
    assert "Abandon the run" in result.output
    assert f"decision={expected}" in result.output


def test_a_browser_that_will_not_open_still_leaves_the_link_and_the_choice(
    attached_terminal: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing_browser = BrowserRecorder(is_launch_successful=False)
    monkeypatch.setattr(pro_offer.webbrowser, "open", failing_browser)

    result = CliRunner().invoke(show_offer_decision, input="1\n2\n")

    assert result.exit_code == 0, result.output
    assert "Could not open a browser" in result.output
    assert f"Visit {PRO_URL}" in result.output
    assert "decision=abandon" in result.output


def test_a_number_outside_the_list_is_refused_and_asked_again(
    attached_terminal: None, browser: BrowserRecorder
) -> None:
    result = CliRunner().invoke(show_offer_decision, input="7\n0\nyes\n1\n2\n")

    assert result.exit_code == 0, result.output
    assert result.output.count("Error:") == 3
    assert browser.opened_urls == [PRO_URL]
    assert "decision=abandon" in result.output


def test_no_color_terminals_get_the_numbered_list_not_the_arrow_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(sys, "stdin", FakeStream(is_terminal=True, typed_text="1\n"))
    monkeypatch.setattr(sys, "stdout", FakeStream(is_terminal=True))
    monkeypatch.setattr(ui, "select_or_abort", UnexpectedMenu())

    assert ui.is_interactive() is False
    assert pro_offer.ask_choice(PRO_INTEREST_QUESTION) == YES_OPTION


def test_no_terminal_means_no_prompt_and_the_run_continues(
    browser: BrowserRecorder,
) -> None:
    # CliRunner's streams are not terminals, so the real gate is exercised here.
    result = CliRunner().invoke(show_offer_decision, input="1\n2\n")

    assert result.output.strip() == "decision=continue"
    assert browser.opened_urls == []


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(sys, "stdin", FakeStream(is_terminal=True))
    monkeypatch.setattr(sys, "stdout", FakeStream(is_terminal=True))

    assert pro_offer.can_prompt_user() is False


@pytest.mark.parametrize(
    "extra_args,env",
    [
        (["--no-promo"], {}),
        ([], {NO_PROMO_ENV_VAR: "1"}),
        (["--dry-run"], {}),
    ],
)
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
    assert ABANDONED_MESSAGE not in result.output
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


def test_abandoning_exits_cleanly_before_the_run_leaves_any_trace(
    tmp_path: Path,
    mock_run_args: list[str],
    attached_terminal: None,
    browser: BrowserRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = CallCounter()
    monkeypatch.setattr(telemetry, "emit_started", started)

    result = CliRunner().invoke(ifixai_cli, mock_run_args, input="1\n2\n")

    assert result.exit_code == 0, result.output
    assert ABANDONED_MESSAGE in result.output
    assert browser.opened_urls == [PRO_URL]
    assert started.call_count == 0
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "reports").exists()


def test_continuing_after_visiting_the_site_runs_the_inspections(
    tmp_path: Path,
    mock_run_args: list[str],
    attached_terminal: None,
    browser: BrowserRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = CallCounter()
    monkeypatch.setattr(telemetry, "emit_started", started)

    result = CliRunner().invoke(ifixai_cli, mock_run_args, input="1\n1\n")

    assert ABANDONED_MESSAGE not in result.output
    assert browser.opened_urls == [PRO_URL]
    assert started.call_count == 1
    assert (tmp_path / "runs").exists()
    assert list((tmp_path / "reports").glob("*.json")), result.output


def test_input_ending_at_the_numbered_prompt_aborts_instead_of_starting_the_run(
    tmp_path: Path,
    mock_run_args: list[str],
    attached_terminal: None,
    browser: BrowserRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = CallCounter()
    monkeypatch.setattr(telemetry, "emit_started", started)

    result = CliRunner().invoke(ifixai_cli, mock_run_args, input="")

    assert result.exit_code == 1, result.output
    assert "Aborted!" in result.output
    assert started.call_count == 0
    assert not (tmp_path / "runs").exists()


def test_declining_at_the_real_prompt_lets_the_run_proceed(
    run_args: list[str], attached_terminal: None, browser: BrowserRecorder
) -> None:
    result = CliRunner().invoke(ifixai_cli, run_args, input="2\n")

    assert result.exit_code == 1, result.output
    assert PRO_URL in result.output
    assert UNKNOWN_TEST_ERROR in result.output
    assert browser.opened_urls == []


@pytest.mark.parametrize(
    "answers,expected,expected_urls",
    [
        ([NO_OPTION], RunDecision.CONTINUE, []),
        ([YES_OPTION, CONTINUE_OPTION], RunDecision.CONTINUE, [PRO_URL]),
        ([YES_OPTION, ABANDON_OPTION], RunDecision.ABANDON, [PRO_URL]),
    ],
)
def test_arrow_menu_answers_drive_the_same_decisions(
    attached_terminal: None,
    browser: BrowserRecorder,
    monkeypatch: pytest.MonkeyPatch,
    answers: list[str],
    expected: RunDecision,
    expected_urls: list[str],
) -> None:
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    monkeypatch.setattr(ui, "select_or_abort", ScriptedMenu(answers))

    assert pro_offer.offer_pro_version() is expected
    assert browser.opened_urls == expected_urls


def test_arrow_menus_start_on_the_answer_that_keeps_the_run_going(
    attached_terminal: None, browser: BrowserRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    menu = ScriptedMenu([YES_OPTION, CONTINUE_OPTION])
    monkeypatch.setattr(ui, "is_interactive", report_terminal_attached)
    monkeypatch.setattr(ui, "select_or_abort", menu)

    pro_offer.offer_pro_version()

    assert menu.defaults == [NO_OPTION, CONTINUE_OPTION]


def test_ctrl_c_on_the_arrow_menu_aborts_instead_of_starting_the_run(
    tmp_path: Path,
    mock_run_args: list[str],
    attached_terminal: None,
    browser: BrowserRecorder,
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
