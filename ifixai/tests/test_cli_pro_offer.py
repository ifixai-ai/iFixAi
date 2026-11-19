"""The free Pro audit offer: a choice at the start of ``ifixai run``, the steps at its end."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import ifixai.cli.run as run_module
from ifixai import telemetry
from ifixai.cli import pro_offer, ui
from ifixai.cli.main import ifixai_cli
from ifixai.cli.pro_offer import NO_PROMO_ENV_VAR
from ifixai.core.types import TestResult, TestRunResult, TestStatus

UNKNOWN_TEST_ID = "ZZ99"
UNKNOWN_TEST_ERROR = "unknown test ID"
REAL_PROVIDER = "openai"
CONNECTOR_URL = "https://mcp.ifixai.ai/mcp"
FAKE_CLAUDE = "/opt/fake/bin/claude"
PRO_ANSWER = "1\n"
OPEN_SOURCE_ANSWER = "2\n"
# The founder's mockups (ticket, 19 Nov), word for word.
START_TEXT = (
    "Even when your AI agent does exactly what you ask, what else does it do along the way? "
    "Try a complimentary iFixAi Pro audit through MCP. We run 12 inspections. You get two "
    "reports showing what happened during testing, what it means for your business, and how "
    "the findings relate to relevant regulations. No payment or subscription required."
)
STEPS_LINES = [
    "Run your free iFixAi Pro audit",
    "1. Add this remote MCP server in your coding agent.",
    "   https://mcp.ifixai.ai/mcp",
    "2. Sign in with Google or Microsoft.",
    '3. In your coding agent, type "Audit my agent with iFixAi."',
    "   When prompted, redeem code SF2026.",
    "The code covers 12 inspections, all findings, and both reports.",
    "No payment or subscription required.",
]
# Kept before the autouse fixtures swap them, for the tests of the real emitters.
REAL_EMIT_SHOWN = telemetry.emit_offer_shown
REAL_EMIT_ACCEPTED = telemetry.emit_offer_accepted
SKIPPED_RUNS = [
    (["--no-promo"], {}),
    ([], {NO_PROMO_ENV_VAR: "1"}),
    (["--dry-run"], {}),
]


class FakeStream(io.StringIO):
    """A text stream that reports whether it is attached to a terminal."""

    def __init__(self, is_terminal: bool) -> None:
        super().__init__()
        self.is_terminal = is_terminal

    def isatty(self) -> bool:
        return self.is_terminal


class EventRecorder:
    """Stands in for a telemetry emitter and records each place."""

    def __init__(self) -> None:
        self.places: list[str] = []

    def __call__(self, where: str) -> None:
        self.places.append(where)


class SenderRecorder:
    """Stands in for the telemetry sender and keeps every body it was given."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, object]] = []

    def submit(self, body: bytes) -> None:
        self.bodies.append(json.loads(body))


class CallRecorder:
    """Stands in for a run hook and records its calls in a shared order list."""

    def __init__(
        self, name: str, order: list[str], reports: Path | None = None, answer: object = None
    ) -> None:
        self.name = name
        self.order = order
        self.reports = reports
        self.answer = answer
        self.calls: list[tuple[object, ...]] = []
        self.reports_on_disk: list[bool] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        self.order.append(self.name)
        if self.reports is not None:
            self.reports_on_disk.append(bool(list(self.reports.glob("*.json"))))
        return self.answer


class ClaudeRecorder:
    """Stands in for ``subprocess.run`` of the ``claude`` CLI: records it, answers as told."""

    def __init__(
        self, returncode: int = 0, stdout: str = "", stderr: str = "",
        raises: Exception | None = None,
    ) -> None:
        self.returncode, self.stdout, self.stderr, self.raises = returncode, stdout, stderr, raises
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def report_outside_ci() -> bool:
    return False


def report_true(*_: object) -> bool:
    return True


def report_false(*_: object) -> bool:
    return False


def no_claude(_: str) -> None:
    return None


def fake_claude(_: str) -> str:
    return FAKE_CLAUDE


def terminal_of(columns: int):
    def size(*_: object, **__: object) -> os.terminal_size:
        return os.terminal_size((columns, 24))
    return size


def run_result(*statuses: TestStatus) -> TestRunResult:
    return TestRunResult(
        test_results=[
            TestResult(test_id=f"B{index:02d}", status=status)
            for index, status in enumerate(statuses, start=1)
        ]
    )


def plain_lines(text: str) -> list[str]:
    return click.unstyle(text).splitlines()


def contains_block(text: str, block: list[str]) -> bool:
    lines = plain_lines(text)
    return any(lines[i:i + len(block)] == block for i in range(len(lines)))


def last_printed_lines(text: str, count: int) -> list[str]:
    return [line for line in plain_lines(text) if line.strip()][-count:]


@click.command()
def start_offer() -> None:
    """Minimal host command so CliRunner can answer the offer's questions."""
    click.echo(f"pro={pro_offer.offer_at_start(REAL_PROVIDER)}")


@pytest.fixture(autouse=True)
def offer_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the shown-once marker lands; kept in ``tmp_path``, never the real home."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "ifixai" / "pro-offer-shown"


@pytest.fixture(autouse=True)
def events(monkeypatch: pytest.MonkeyPatch) -> dict[str, EventRecorder]:
    shown, accepted = EventRecorder(), EventRecorder()
    monkeypatch.setattr(telemetry, "emit_offer_shown", shown)
    monkeypatch.setattr(telemetry, "emit_offer_accepted", accepted)
    return {"shown": shown, "accepted": accepted}


@pytest.fixture(autouse=True)
def without_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``claude`` on the PATH unless a test puts its fake there; the real one never runs."""
    monkeypatch.setattr(pro_offer.shutil, "which", no_claude)
    monkeypatch.setattr(pro_offer.subprocess, "run", ClaudeRecorder(raises=AssertionError()))


@pytest.fixture
def person_answering(monkeypatch: pytest.MonkeyPatch) -> None:
    """A person at a terminal, outside CI, answering the typed menu (CliRunner's input)."""
    monkeypatch.setattr(pro_offer, "can_ask_user", report_true)
    monkeypatch.setattr(ui, "is_interactive", report_false)


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every stream counts as a terminal, outside CI; the output itself is read with capsys."""
    monkeypatch.setattr(pro_offer, "is_terminal", report_true)
    monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)


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


@pytest.fixture
def mock_counts_as_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mock agent stands in for a real one, so the offer applies to an offline run."""
    monkeypatch.setattr(pro_offer, "MOCK_PROVIDER", "no-such-provider")


# --- Layout -------------------------------------------------------------------------------


@pytest.mark.parametrize("columns", [80, 100, 30])
def test_the_start_screen_wraps_at_word_boundaries_within_the_terminal(
    monkeypatch: pytest.MonkeyPatch, columns: int
) -> None:
    monkeypatch.setattr(pro_offer.shutil, "get_terminal_size", terminal_of(columns))

    lines = plain_lines(pro_offer.render_screen(
        pro_offer.START_HEADLINE, pro_offer.START_BODY, FakeStream(is_terminal=False)
    ))

    assert " ".join(lines).split() == START_TEXT.split()
    assert [line for line in lines if len(line) >= columns] == []


@pytest.mark.parametrize("columns", [80, 100])
def test_the_steps_screen_reads_as_the_mockup(
    monkeypatch: pytest.MonkeyPatch, columns: int
) -> None:
    monkeypatch.setattr(pro_offer.shutil, "get_terminal_size", terminal_of(columns))

    text = pro_offer.render_screen(
        pro_offer.STEPS_HEADLINE, pro_offer.STEPS_BODY, FakeStream(is_terminal=False)
    )

    assert plain_lines(text) == STEPS_LINES
    assert text == click.unstyle(text)


def test_a_narrow_terminal_never_breaks_the_url_or_the_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pro_offer.shutil, "get_terminal_size", terminal_of(24))

    lines = plain_lines(pro_offer.render_screen(
        pro_offer.STEPS_HEADLINE, pro_offer.STEPS_BODY, FakeStream(is_terminal=False)
    ))

    assert f"   {CONNECTOR_URL}" in lines
    assert any("SF2026." in line.split() for line in lines)
    assert " ".join(lines).split() == " ".join(STEPS_LINES).split()


def test_colour_marks_the_url_and_the_code_only_where_the_terminal_shows_colour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(pro_offer.shutil, "get_terminal_size", terminal_of(80))
    steps = (pro_offer.STEPS_HEADLINE, pro_offer.STEPS_BODY)

    coloured = pro_offer.render_screen(*steps, FakeStream(is_terminal=True))
    monkeypatch.setenv("NO_COLOR", "1")
    plain = pro_offer.render_screen(*steps, FakeStream(is_terminal=True))

    assert click.style(CONNECTOR_URL, fg="cyan") in coloured
    assert click.style("SF2026", fg=(232, 99, 42), bold=True) in coloured
    assert click.style("Run your free iFixAi Pro audit", bold=True) in coloured
    assert plain_lines(coloured) == STEPS_LINES
    assert plain == click.unstyle(plain)


# --- The start choice -----------------------------------------------------------------------


def test_open_source_carries_on_and_the_offer_is_shown_once(
    person_answering: None, offer_marker: Path, events: dict[str, EventRecorder]
) -> None:
    first = CliRunner().invoke(start_offer, input=OPEN_SOURCE_ANSWER)
    second = CliRunner().invoke(start_offer, input="")

    assert first.exit_code == 0, first.output
    assert "Try a complimentary iFixAi Pro audit through MCP." in first.output
    assert "Continue with a free Pro audit" in first.output
    assert "Continue with open source" in first.output
    assert "pro=False" in first.output
    assert "Run your free iFixAi Pro audit" not in first.output
    assert offer_marker.exists()
    assert second.output.strip() == "pro=False"
    assert events["shown"].places == ["start"]
    assert events["accepted"].places == []


@pytest.mark.parametrize("answer", [PRO_ANSWER, "\n"])
def test_a_pro_pick_prints_the_steps_and_is_counted(
    person_answering: None, events: dict[str, EventRecorder], answer: str
) -> None:
    result = CliRunner().invoke(start_offer, input=answer)

    assert result.exit_code == 0, result.output
    assert contains_block(result.output, STEPS_LINES), result.output
    assert "pro=True" in result.output
    assert "Claude Code" not in result.output
    assert events["shown"].places == ["start"]
    assert events["accepted"].places == ["start"]


def test_the_arrow_menu_answers_the_same_choice(
    monkeypatch: pytest.MonkeyPatch, events: dict[str, EventRecorder]
) -> None:
    asked: list[tuple[str, tuple[str, ...], str]] = []

    def menu(message: str, choices: tuple[str, ...], default: str) -> str:
        asked.append((message, tuple(choices), default))
        return pro_offer.OPEN_SOURCE_OPTION

    monkeypatch.setattr(pro_offer, "can_ask_user", report_true)
    monkeypatch.setattr(ui, "is_interactive", report_true)
    monkeypatch.setattr(ui, "select_or_abort", menu)

    assert pro_offer.offer_at_start(REAL_PROVIDER) is False
    assert asked == [(
        "How would you like to continue?",
        ("Continue with a free Pro audit", "Continue with open source"),
        "Continue with a free Pro audit",
    )]


@pytest.mark.parametrize("provider", ["mock", "MOCK"])
def test_the_mock_agent_never_sees_the_offer(
    person_answering: None, offer_marker: Path, events: dict[str, EventRecorder], provider: str
) -> None:
    assert pro_offer.offer_at_start(provider) is False
    assert not offer_marker.exists()
    assert events["shown"].places == []


@pytest.mark.parametrize(
    "is_stdin_terminal,is_stdout_terminal,is_stderr_terminal,expected",
    [
        (True, True, True, True),
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
    ],
)
def test_asking_needs_a_terminal_on_every_stream(
    monkeypatch: pytest.MonkeyPatch,
    is_stdin_terminal: bool,
    is_stdout_terminal: bool,
    is_stderr_terminal: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)
    monkeypatch.setattr(sys, "stdin", FakeStream(is_stdin_terminal))
    monkeypatch.setattr(sys, "stdout", FakeStream(is_stdout_terminal))
    monkeypatch.setattr(sys, "stderr", FakeStream(is_stderr_terminal))

    assert pro_offer.can_ask_user() is expected


def test_ci_is_never_asked_even_with_a_terminal(
    monkeypatch: pytest.MonkeyPatch, offer_marker: Path
) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(sys, "stdin", FakeStream(is_terminal=True))
    monkeypatch.setattr(sys, "stdout", FakeStream(is_terminal=True))

    assert pro_offer.can_ask_user() is False
    assert pro_offer.offer_at_start(REAL_PROVIDER) is False
    assert not offer_marker.exists()


@pytest.mark.parametrize("blocking_file", ["config", "config/ifixai"])
def test_a_marker_that_cannot_be_written_skips_the_offer(
    tmp_path: Path, person_answering: None, blocking_file: str
) -> None:
    blocker = tmp_path / blocking_file
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory", encoding="utf-8")

    result = CliRunner().invoke(start_offer, input="")

    assert result.output.strip() == "pro=False"


# --- Adding the connector to Claude Code ----------------------------------------------------


def test_with_claude_installed_a_pro_pick_adds_the_connector_on_a_yes(
    person_answering: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = ClaudeRecorder(returncode=0, stdout="Added HTTP MCP server ifixai")
    monkeypatch.setattr(pro_offer.shutil, "which", fake_claude)
    monkeypatch.setattr(pro_offer.subprocess, "run", claude)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + "1\n")

    assert result.exit_code == 0, result.output
    assert "Add it to Claude Code for me" in result.output
    assert "I'll add it myself" in result.output
    assert [args for args, _ in claude.calls] == [[
        FAKE_CLAUDE, "mcp", "add", "--transport", "http", "--scope", "user", "ifixai",
        CONNECTOR_URL,
    ]]
    assert "shell" not in claude.calls[0][1]
    assert claude.calls[0][1]["timeout"] == pro_offer.CLAUDE_ADD_TIMEOUT_SECONDS
    assert "Added the iFixAi server to Claude Code." in result.output
    assert result.output.rstrip().endswith("Restart Claude Code, then do steps 2 and 3.\npro=True")


def test_an_existing_ifixai_server_counts_as_added(
    person_answering: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = ClaudeRecorder(returncode=1, stderr="MCP server ifixai already exists in user config")
    monkeypatch.setattr(pro_offer.shutil, "which", fake_claude)
    monkeypatch.setattr(pro_offer.subprocess, "run", claude)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + "\n")

    assert "Added the iFixAi server to Claude Code." in result.output
    assert "Could not add it" not in result.output


@pytest.mark.parametrize(
    "claude",
    [
        ClaudeRecorder(returncode=1, stderr="Error: invalid transport"),
        ClaudeRecorder(raises=FileNotFoundError("claude")),
        ClaudeRecorder(raises=subprocess.TimeoutExpired("claude", 60)),
    ],
    ids=["refused", "missing", "timed-out"],
)
def test_a_failed_add_prints_the_command_to_run_by_hand(
    person_answering: None, monkeypatch: pytest.MonkeyPatch, claude: ClaudeRecorder
) -> None:
    monkeypatch.setattr(pro_offer.shutil, "which", fake_claude)
    monkeypatch.setattr(pro_offer.subprocess, "run", claude)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + "1\n")

    assert result.exit_code == 0, result.output
    assert "Could not add it. Run this yourself:" in result.output
    assert (
        "  claude mcp add --transport http --scope user ifixai https://mcp.ifixai.ai/mcp"
        in plain_lines(result.output)
    )
    assert "Added the iFixAi server" not in result.output
    assert "Restart Claude Code, then do steps 2 and 3." in result.output


def test_adding_it_myself_runs_nothing(
    person_answering: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = ClaudeRecorder()
    monkeypatch.setattr(pro_offer.shutil, "which", fake_claude)
    monkeypatch.setattr(pro_offer.subprocess, "run", claude)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + "2\n")

    assert claude.calls == []
    assert "pro=True" in result.output
    assert "Restart Claude Code" not in result.output


@pytest.mark.parametrize(
    "question", [pro_offer.START_QUESTION, pro_offer.CLAUDE_CODE_QUESTION], ids=["start", "claude"]
)
def test_each_question_with_its_longest_answer_fits_an_80_column_line(
    question: pro_offer.ChoiceQuestion,
) -> None:
    # The arrow menu echoes "? <question> <answer>" on one line after the pick.
    longest = max(len(f"? {question['message']} {option}") for option in question["options"])

    assert longest < 80


def test_open_source_never_offers_the_claude_code_setup(
    person_answering: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = ClaudeRecorder()
    monkeypatch.setattr(pro_offer.shutil, "which", fake_claude)
    monkeypatch.setattr(pro_offer.subprocess, "run", claude)

    result = CliRunner().invoke(start_offer, input=OPEN_SOURCE_ANSWER)

    assert "Claude Code" not in result.output
    assert claude.calls == []


# --- The end of a run -----------------------------------------------------------------------


def test_the_steps_print_on_stderr_after_every_real_run_with_a_verdict(
    terminal: None,
    events: dict[str, EventRecorder],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pro_offer.shutil, "which", fake_claude)

    pro_offer.offer_at_end(REAL_PROVIDER, run_result(TestStatus.PASS))
    pro_offer.offer_at_end(REAL_PROVIDER, run_result(TestStatus.FAIL, TestStatus.ERROR))

    printed = capsys.readouterr()
    assert printed.out == ""
    assert [line for line in plain_lines(printed.err) if line] == STEPS_LINES * 2
    assert events["shown"].places == ["end", "end"]
    assert events["accepted"].places == []


@pytest.mark.parametrize(
    "statuses",
    [(), (TestStatus.INCONCLUSIVE,), (TestStatus.ERROR, TestStatus.INCONCLUSIVE)],
)
def test_the_end_steps_need_at_least_one_verdict(
    terminal: None, capsys: pytest.CaptureFixture[str], statuses: tuple[TestStatus, ...]
) -> None:
    pro_offer.offer_at_end(REAL_PROVIDER, run_result(*statuses))

    assert capsys.readouterr().err == ""


def test_the_end_steps_skip_the_mock_agent_and_ci(
    terminal: None, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pro_offer.offer_at_end("mock", run_result(TestStatus.PASS))
    monkeypatch.setattr(telemetry, "in_ci", report_true)
    pro_offer.offer_at_end(REAL_PROVIDER, run_result(TestStatus.PASS))

    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "is_stdout_terminal,is_stderr_terminal,expected",
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_the_end_steps_need_a_terminal_on_both_output_streams(
    monkeypatch: pytest.MonkeyPatch,
    is_stdout_terminal: bool,
    is_stderr_terminal: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)
    monkeypatch.setattr(sys, "stdout", FakeStream(is_stdout_terminal))
    monkeypatch.setattr(sys, "stderr", FakeStream(is_stderr_terminal))

    assert pro_offer.person_at_terminal() is expected


# --- Inside a run -----------------------------------------------------------------------------


def test_a_pro_pick_ends_the_run_before_anything_is_sent(
    tmp_path: Path,
    mock_run_args: list[str],
    person_answering: None,
    mock_counts_as_real: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_agent(*_: object, **__: object) -> None:
        raise AssertionError("the agent was contacted after a Pro pick")

    monkeypatch.setattr(run_module, "resolve_provider", no_agent)

    result = CliRunner().invoke(ifixai_cli, mock_run_args, input=PRO_ANSWER)

    assert result.exit_code == 0, result.output
    assert contains_block(result.output, STEPS_LINES), result.output
    assert "Reports saved" not in result.output
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / "runs").exists()


def test_open_source_runs_and_the_run_ends_on_the_steps(
    tmp_path: Path,
    mock_run_args: list[str],
    person_answering: None,
    mock_counts_as_real: None,
    offer_marker: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pro_offer, "person_at_terminal", report_true)

    first = CliRunner().invoke(ifixai_cli, mock_run_args, input=OPEN_SOURCE_ANSWER)
    second = CliRunner().invoke(ifixai_cli, mock_run_args, input="")

    assert "Try a complimentary iFixAi Pro audit through MCP." in first.output
    assert "Try a complimentary" not in second.output
    assert offer_marker.exists()
    for run in (first, second):
        assert "Reports saved" in run.output, run.output
        assert last_printed_lines(run.output, len(STEPS_LINES)) == STEPS_LINES, run.output
    for report in (tmp_path / "reports").iterdir():
        assert "SF2026" not in report.read_text(encoding="utf-8")


def test_the_run_offers_at_the_start_and_last_after_the_saved_reports(
    tmp_path: Path, mock_run_args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    start = CallRecorder("start", order, answer=False)
    end = CallRecorder("end", order, reports=tmp_path / "reports")
    monkeypatch.setattr(run_module, "offer_at_start", start)
    monkeypatch.setattr(run_module, "offer_at_end", end)
    monkeypatch.setattr(telemetry, "emit_completed", CallRecorder("completed", order))

    result = CliRunner().invoke(ifixai_cli, mock_run_args)

    # The completed event only queues a send and prints nothing; the steps print last.
    assert order == ["start", "completed", "end"], result.output
    assert start.calls == [("mock",)]
    assert end.calls[0][0] == "mock"
    assert isinstance(end.calls[0][1], TestRunResult)
    assert end.reports_on_disk == [True]


@pytest.mark.parametrize("extra_args,env", SKIPPED_RUNS)
def test_the_run_skips_the_offer_when_told_to_or_when_nothing_will_run(
    run_args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    env: dict[str, str],
) -> None:
    order: list[str] = []
    monkeypatch.setattr(run_module, "offer_at_start", CallRecorder("start", order, answer=True))
    monkeypatch.setattr(run_module, "offer_at_end", CallRecorder("end", order))

    result = CliRunner().invoke(ifixai_cli, [*run_args, *extra_args], env=env)

    assert order == []
    assert UNKNOWN_TEST_ERROR in result.output


def test_no_promo_skips_the_end_steps_of_a_finished_run(
    mock_run_args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(run_module, "offer_at_start", CallRecorder("start", order, answer=True))
    monkeypatch.setattr(run_module, "offer_at_end", CallRecorder("end", order))

    result = CliRunner().invoke(ifixai_cli, [*mock_run_args, "--no-promo"])

    assert "Reports saved" in result.output
    assert order == []


def test_a_run_without_a_terminal_never_shows_or_marks_the_offer(
    mock_run_args: list[str], offer_marker: Path, mock_counts_as_real: None
) -> None:
    result = CliRunner().invoke(ifixai_cli, mock_run_args)

    assert "Reports saved" in result.output
    assert "iFixAi Pro" not in result.output
    assert not offer_marker.exists()


# --- Telemetry ------------------------------------------------------------------------------


class TestOfferTelemetry:
    @pytest.fixture
    def sender(self, monkeypatch: pytest.MonkeyPatch) -> SenderRecorder:
        recorder = SenderRecorder()
        for name in ("IFIXAI_TELEMETRY", "DO_NOT_TRACK"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(telemetry, "emit_offer_shown", REAL_EMIT_SHOWN)
        monkeypatch.setattr(telemetry, "emit_offer_accepted", REAL_EMIT_ACCEPTED)
        monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)
        monkeypatch.setattr(telemetry, "_force_disabled", False)
        monkeypatch.setattr(telemetry, "_offers_emitted", set())
        monkeypatch.setattr(telemetry, "_acceptances_emitted", set())
        monkeypatch.setattr(telemetry, "_get_sender", lambda: recorder)
        return recorder

    def test_each_event_is_sent_once_per_place_with_where_it_was(
        self, sender: SenderRecorder
    ) -> None:
        telemetry.emit_offer_shown("start")
        telemetry.emit_offer_accepted("start")
        telemetry.emit_offer_shown("end")
        telemetry.emit_offer_shown("end")
        telemetry.emit_offer_accepted("start")

        assert [(b["event"], b["properties"]["where"]) for b in sender.bodies] == [
            ("ifixai_offer_shown", "start"),
            ("ifixai_offer_accepted", "start"),
            ("ifixai_offer_shown", "end"),
        ]

    def test_an_unknown_place_is_never_sent(self, sender: SenderRecorder) -> None:
        telemetry.emit_offer_shown("/home/someone/project")
        telemetry.emit_offer_accepted("claude mcp add")

        assert sender.bodies == []

    def test_other_events_carry_no_where(self, sender: SenderRecorder) -> None:
        telemetry._enqueue("ifixai_started")

        assert "where" not in sender.bodies[0]["properties"]

    def test_the_opt_out_sends_nothing(
        self, sender: SenderRecorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IFIXAI_TELEMETRY", "0")

        telemetry.emit_offer_shown("start")
        telemetry.emit_offer_accepted("start")

        assert sender.bodies == []
