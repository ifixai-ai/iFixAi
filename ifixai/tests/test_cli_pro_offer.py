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


class CommandRecorder:
    """Stands in for ``shutil.which`` and ``subprocess.run`` of the agent CLIs.

    ``installed`` maps a command name to its answer: (exit code, stdout, stderr), or
    an exception to raise. Every call is recorded; a command not installed is not found.
    """

    def __init__(self) -> None:
        self.installed: dict[str, tuple[int, str, str] | Exception] = {}
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def which(self, name: str) -> str | None:
        return f"/fake/bin/{name}" if name in self.installed else None

    def __call__(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        answer = self.installed[Path(args[0]).name]
        if isinstance(answer, Exception):
            raise answer
        return subprocess.CompletedProcess(args, *answer)


def report_outside_ci() -> bool:
    return False


def report_true(*_: object) -> bool:
    return True


def report_false(*_: object) -> bool:
    return False


def no_command(_: str) -> None:
    return None


def fake_command(name: str) -> str:
    return f"/fake/bin/{name}"


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
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty home folder and no agent CLI on the PATH, unless a test adds them.

    The real home, the real agents and their files are never seen or run.
    """
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    monkeypatch.setattr(pro_offer.shutil, "which", no_command)
    monkeypatch.setattr(pro_offer.subprocess, "run", CommandRecorder())
    return folder


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
    assert "Found on this computer" not in result.output
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


# --- Adding the server to the coding agents found ---------------------------------------------

ADD_FOR_ME = "1\n"
ADD_MYSELF = "2\n"
CLAUDE_ADD = [
    "mcp", "add", "--transport", "http", "--scope", "user", "ifixai", CONNECTOR_URL,
]
CLAUDE_BY_HAND = (
    "  claude mcp add --transport http --scope user ifixai https://mcp.ifixai.ai/mcp"
)
CODEX_BY_HAND = "  codex mcp add ifixai --url https://mcp.ifixai.ai/mcp"
CODEX_TABLE = b'\n[mcp_servers.ifixai]\nurl = "https://mcp.ifixai.ai/mcp"\n'
CODEX_CONFIG = b'# mine\nmodel = "o3"\n\n[mcp_servers.other]\ncommand = "other-server"'
CURSOR_CONFIG = {"mcpServers": {"other": {"url": "https://other.example/mcp"}}, "theme": "dark"}
RESTART = "Restart your coding agent, then do steps 2 and 3."
needs_tomllib = pytest.mark.skipif(pro_offer.tomllib is None, reason="tomllib is Python 3.11+")


def json_bytes(config: object) -> bytes:
    return (json.dumps(config, indent=2) + "\n").encode("utf-8")


def backup_of(path: Path) -> Path:
    return path.with_name(path.name + ".ifixai-backup")


def after_steps(output: str) -> list[str]:
    """The lines printed after the steps screen, without the host command's own line."""
    lines = plain_lines(output)
    steps_end = len(lines) - lines[::-1].index(STEPS_LINES[-1])
    rest = lines[steps_end:]
    return [line for line in rest if line and not line.startswith("pro=")]


@pytest.fixture
def agent_cli(monkeypatch: pytest.MonkeyPatch) -> CommandRecorder:
    """Agent CLIs on the PATH: put a name in ``installed`` and set its answer."""
    recorder = CommandRecorder()
    monkeypatch.setattr(pro_offer.shutil, "which", recorder.which)
    monkeypatch.setattr(pro_offer.subprocess, "run", recorder)
    return recorder


@pytest.fixture
def codex_home(home: Path) -> Path:
    folder = home / ".codex"
    folder.mkdir()
    return folder


@pytest.fixture
def cursor_file(home: Path) -> Path:
    (home / ".cursor").mkdir()
    path = home / ".cursor" / "mcp.json"
    path.write_bytes(json_bytes(CURSOR_CONFIG))
    return path


@needs_tomllib
def test_one_key_adds_the_server_to_every_agent_found(
    person_answering: None, agent_cli: CommandRecorder, codex_home: Path, cursor_file: Path
) -> None:
    agent_cli.installed["claude"] = (0, "Added HTTP MCP server ifixai to user config", "")
    (codex_home / "config.toml").write_bytes(CODEX_CONFIG)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert result.exit_code == 0, result.output
    assert after_steps(result.output)[:4] == [
        "Found on this computer: Claude Code, Codex, Cursor.",
        "  [1] Add it for me",
        "  [2] I'll add it myself",
        "Add the iFixAi server to them now? [1]: 1",
    ]
    assert after_steps(result.output)[4:] == [
        "Added to Claude Code.", "Added to Codex.", "Added to Cursor.", RESTART,
    ]
    [(args, kwargs)] = agent_cli.calls
    assert args == ["/fake/bin/claude", *CLAUDE_ADD]
    assert "shell" not in kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["timeout"] == pro_offer.ADD_TIMEOUT_SECONDS
    assert (codex_home / "config.toml").read_bytes() == CODEX_CONFIG + CODEX_TABLE
    assert backup_of(codex_home / "config.toml").read_bytes() == CODEX_CONFIG
    cursor = json.loads(cursor_file.read_text(encoding="utf-8"))
    assert cursor == {
        "mcpServers": {**CURSOR_CONFIG["mcpServers"], "ifixai": {"url": CONNECTOR_URL}},
        "theme": "dark",
    }
    assert backup_of(cursor_file).read_bytes() == json_bytes(CURSOR_CONFIG)


def test_no_agent_found_means_no_question(
    person_answering: None, agent_cli: CommandRecorder
) -> None:
    result = CliRunner().invoke(start_offer, input=PRO_ANSWER)

    assert result.exit_code == 0, result.output
    assert after_steps(result.output) == []
    assert last_printed_lines(result.output, 2) == [STEPS_LINES[-1], "pro=True"]


def test_one_agent_is_asked_about_as_it(
    person_answering: None, agent_cli: CommandRecorder
) -> None:
    agent_cli.installed["claude"] = (0, "", "")

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert "Found on this computer: Claude Code." in result.output
    assert "Add the iFixAi server to it now?" in result.output


@pytest.mark.parametrize(
    "answer",
    [(1, "", "MCP server ifixai already exists in user config"), (0, "", "")],
    ids=["already-exists", "added"],
)
def test_a_command_that_adds_or_already_has_it_says_added(
    person_answering: None, agent_cli: CommandRecorder, answer: tuple[int, str, str]
) -> None:
    agent_cli.installed["claude"] = answer

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert after_steps(result.output)[-2:] == ["Added to Claude Code.", RESTART]
    assert "Could not add" not in result.output


@pytest.mark.parametrize(
    "answer",
    [
        (1, "", "Error: invalid transport"),
        FileNotFoundError("claude"),
        subprocess.TimeoutExpired("claude", 60),
    ],
    ids=["refused", "missing", "timed-out"],
)
def test_a_failed_command_prints_its_line_after_the_others_are_added(
    person_answering: None,
    agent_cli: CommandRecorder,
    cursor_file: Path,
    answer: tuple[int, str, str] | Exception,
) -> None:
    agent_cli.installed["claude"] = answer

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert result.exit_code == 0, result.output
    assert after_steps(result.output)[-4:] == [
        "Added to Cursor.",
        "Could not add it to Claude Code. Run this yourself:",
        CLAUDE_BY_HAND,
        RESTART,
    ]
    assert "ifixai" in json.loads(cursor_file.read_text(encoding="utf-8"))["mcpServers"]


def test_adding_it_myself_runs_and_writes_nothing(
    person_answering: None, agent_cli: CommandRecorder, codex_home: Path, cursor_file: Path
) -> None:
    agent_cli.installed["claude"] = (0, "", "")
    (codex_home / "config.toml").write_bytes(CODEX_CONFIG)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_MYSELF)

    assert result.exit_code == 0, result.output
    assert agent_cli.calls == []
    assert (codex_home / "config.toml").read_bytes() == CODEX_CONFIG
    assert cursor_file.read_bytes() == json_bytes(CURSOR_CONFIG)
    assert sorted(p.name for p in codex_home.iterdir()) == ["config.toml"]
    assert RESTART not in result.output


def test_open_source_never_offers_to_add_the_server(
    person_answering: None, agent_cli: CommandRecorder, cursor_file: Path
) -> None:
    agent_cli.installed["claude"] = (0, "", "")

    result = CliRunner().invoke(start_offer, input=OPEN_SOURCE_ANSWER)

    assert "Found on this computer" not in result.output
    assert agent_cli.calls == []


@pytest.mark.parametrize("columns", [80, 40])
def test_every_line_fits_the_terminal_except_the_lines_to_copy(
    person_answering: None,
    agent_cli: CommandRecorder,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    columns: int,
) -> None:
    monkeypatch.setattr(pro_offer.shutil, "get_terminal_size", terminal_of(columns))
    # Where it is when unset, so opencode's file is named from the home folder.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for name in ("claude", "codex", "gemini", "amp", "opencode", "cline"):
        agent_cli.installed[name] = (1, "", "Error")
    for folder in (".cursor", ".copilot", ".kiro", ".junie", ".warp", ".gemini/config"):
        (home / folder).mkdir(parents=True)
    for agent in pro_offer.AGENTS:
        if isinstance(agent, pro_offer.JsonFileAgent) and agent.folder().is_dir():
            agent.config_path().parent.mkdir(exist_ok=True)
            agent.config_path().write_text("// not JSON", encoding="utf-8")

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    printed = after_steps(result.output)
    to_copy = [line for line in printed if line.startswith("  ") and line[2] != "["]
    assert len(to_copy) == 12, result.output
    assert " ".join(printed).endswith(RESTART)
    # Two answers echoed by the typed menu follow its question on one line.
    wide = [line for line in printed if line not in to_copy and len(line) >= columns]
    assert [line for line in wide if not line.startswith("Add the iFixAi server")] == []


@pytest.mark.parametrize("count", [1, 2])
def test_the_start_and_agent_questions_fit_an_80_column_line(count: int) -> None:
    # The arrow menu echoes "? <question> <answer>" on one line after the pick.
    for question in (pro_offer.START_QUESTION, pro_offer.agents_question(count)):
        longest = max(len(f"? {question['message']} {option}") for option in question["options"])
        assert longest < 80


@pytest.fixture
def config_in_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The XDG config folder where it is when unset: ``~/.config``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home / ".config"


def test_the_agents_are_named_in_the_table_order(
    agent_cli: CommandRecorder, home: Path, config_in_home: Path
) -> None:
    for name in ("opencode", "claude", "gemini"):
        agent_cli.installed[name] = (0, "", "")
    for folder in (".config/devin", ".warp", ".cursor"):
        (home / folder).mkdir(parents=True)

    assert [agent.name for agent in pro_offer.AGENTS if agent.found()] == [
        "Claude Code", "Gemini CLI", "opencode", "Cursor", "Warp", "Devin Desktop",
    ]


@pytest.mark.parametrize(
    "cli,folder,skipped",
    [("copilot", ".copilot", "VS Code Copilot"), ("devin", ".config/devin", "Devin Desktop")],
)
def test_a_file_shared_with_an_installed_cli_is_left_to_that_cli(
    agent_cli: CommandRecorder, home: Path, config_in_home: Path, cli: str, folder: str,
    skipped: str,
) -> None:
    (home / folder).mkdir(parents=True)
    agent_cli.installed[cli] = (0, "", "")

    names = [agent.name for agent in pro_offer.AGENTS if agent.found()]

    assert skipped not in names
    assert len(names) == 1


@pytest.mark.parametrize(
    "agent_name,file,entry",
    [
        ("Cursor", ".cursor/mcp.json", {"url": CONNECTOR_URL}),
        ("VS Code Copilot", ".copilot/mcp-config.json",
         {"type": "http", "url": CONNECTOR_URL, "tools": ["*"]}),
        ("Kiro", ".kiro/settings/mcp.json", {"url": CONNECTOR_URL}),
        ("Junie", ".junie/mcp/mcp.json", {"url": CONNECTOR_URL}),
        ("Warp", ".warp/.mcp.json", {"url": CONNECTOR_URL}),
        ("Antigravity CLI", ".gemini/config/mcp_config.json", {"serverUrl": CONNECTOR_URL}),
        ("Devin Desktop", ".config/devin/mcp_config.json",
         {"url": CONNECTOR_URL, "transport": "http"}),
    ],
)
def test_each_file_agent_gets_its_documented_entry_in_a_new_file(
    agent_cli: CommandRecorder,
    home: Path,
    config_in_home: Path,
    agent_name: str,
    file: str,
    entry: dict[str, object],
) -> None:
    [agent] = [a for a in pro_offer.AGENTS if a.name == agent_name]
    assert isinstance(agent, pro_offer.JsonFileAgent)
    agent.folder().mkdir(parents=True)

    assert agent.add() is True
    assert json.loads((home / file).read_text(encoding="utf-8")) == {
        "mcpServers": {"ifixai": entry}
    }
    assert not backup_of(home / file).exists()


DEVIN_ENTRY = {"url": CONNECTOR_URL, "transport": "http"}
DEVIN_BY_HAND = "  devin mcp add -s user ifixai https://mcp.ifixai.ai/mcp"


def test_devin_cli_gets_its_user_file_and_is_never_run(
    person_answering: None, agent_cli: CommandRecorder, config_in_home: Path
) -> None:
    # Its own `mcp add` writes this file, then waits for a browser sign-in past the limit.
    agent_cli.installed["devin"] = (0, "", "")
    (config_in_home / "devin").mkdir(parents=True)
    path = config_in_home / "devin" / "mcp_config.json"
    path.write_bytes(json_bytes({"mcpServers": {"other": {"url": "https://other.example"}}}))

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert result.exit_code == 0, result.output
    assert after_steps(result.output)[0] == "Found on this computer: Devin CLI."
    assert after_steps(result.output)[-2:] == ["Added to Devin CLI.", RESTART]
    assert agent_cli.calls == []
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "mcpServers": {"other": {"url": "https://other.example"}, "ifixai": DEVIN_ENTRY}
    }


def test_devin_cli_without_its_folder_gets_the_command_and_no_folder(
    person_answering: None, agent_cli: CommandRecorder, config_in_home: Path
) -> None:
    agent_cli.installed["devin"] = (0, "", "")

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert after_steps(result.output)[-3:] == [
        "Could not add it to Devin CLI. Run this yourself:", DEVIN_BY_HAND, RESTART,
    ]
    assert not (config_in_home / "devin").exists()
    assert agent_cli.calls == []


def test_devin_follows_xdg_config_home_and_appdata_on_windows(
    agent_cli: CommandRecorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    assert pro_offer.devin_config_folder() == tmp_path / "xdg" / "devin"

    monkeypatch.setattr(pro_offer.sys, "platform", "win32")
    assert pro_offer.devin_config_folder() == tmp_path / "appdata" / "devin"


def test_copilot_home_moves_the_vs_code_copilot_file(
    agent_cli: CommandRecorder, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "copilot-elsewhere"
    elsewhere.mkdir()
    (home / ".copilot").mkdir()
    monkeypatch.setenv("COPILOT_HOME", str(elsewhere))
    [agent] = [a for a in pro_offer.AGENTS if a.name == "VS Code Copilot"]

    assert agent.found() is True
    assert agent.add() is True
    assert "ifixai" in json.loads((elsewhere / "mcp-config.json").read_text())["mcpServers"]
    assert list((home / ".copilot").iterdir()) == []


def test_a_file_agent_without_its_own_folder_is_not_found_and_nothing_is_created(
    agent_cli: CommandRecorder, home: Path
) -> None:
    assert [agent for agent in pro_offer.AGENTS if agent.found()] == []
    assert list(home.iterdir()) == []


@pytest.mark.parametrize(
    "content",
    [
        b'{\n  // my servers\n  "mcpServers": {}\n}\n',
        b"{broken",
        b"[1, 2]",
        b'{"mcpServers": ["a"]}',
        b"\xff\xfe not utf-8",
    ],
    ids=["comments", "broken", "not-an-object", "servers-not-an-object", "not-utf-8"],
)
def test_a_file_that_does_not_parse_is_left_alone_with_the_line_to_add(
    person_answering: None, agent_cli: CommandRecorder, cursor_file: Path, content: bytes
) -> None:
    cursor_file.write_bytes(content)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert result.exit_code == 0, result.output
    assert cursor_file.read_bytes() == content
    assert not backup_of(cursor_file).exists()
    assert after_steps(result.output)[-3:] == [
        'Could not add it to Cursor. Add this under "mcpServers" in ~/.cursor/mcp.json:',
        '  "ifixai": {"url": "https://mcp.ifixai.ai/mcp"}',
        RESTART,
    ]


def test_an_existing_ifixai_entry_counts_as_added_and_is_never_changed(
    person_answering: None, agent_cli: CommandRecorder, cursor_file: Path
) -> None:
    mine = b'{"mcpServers":{"ifixai":{"url":"https://my.own/mcp"}}}'
    cursor_file.write_bytes(mine)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert after_steps(result.output)[-2:] == ["Added to Cursor.", RESTART]
    assert cursor_file.read_bytes() == mine
    assert not backup_of(cursor_file).exists()


def test_an_empty_file_gains_the_entry(agent_cli: CommandRecorder, cursor_file: Path) -> None:
    cursor_file.write_bytes(b"\n")

    assert pro_offer.add_json_entry(cursor_file, {"url": CONNECTOR_URL}) is True
    assert json.loads(cursor_file.read_text(encoding="utf-8")) == {
        "mcpServers": {"ifixai": {"url": CONNECTOR_URL}}
    }


def test_a_symlinked_file_is_written_through_and_keeps_its_mode(
    agent_cli: CommandRecorder, cursor_file: Path, tmp_path: Path
) -> None:
    real = tmp_path / "dotfiles" / "cursor-mcp.json"
    real.parent.mkdir()
    real.write_bytes(json_bytes(CURSOR_CONFIG))
    real.chmod(0o640)
    cursor_file.unlink()
    cursor_file.symlink_to(real)

    assert pro_offer.add_json_entry(cursor_file, {"url": CONNECTOR_URL}) is True
    assert cursor_file.is_symlink()
    assert "ifixai" in json.loads(real.read_text(encoding="utf-8"))["mcpServers"]
    assert real.stat().st_mode & 0o777 == 0o640
    assert backup_of(real).read_bytes() == json_bytes(CURSOR_CONFIG)


def test_a_failed_write_leaves_the_old_file_and_no_temporary_file(
    agent_cli: CommandRecorder, cursor_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full_disk(_: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pro_offer.os, "fsync", full_disk)

    cursor = pro_offer.JsonFileAgent(
        "Cursor", pro_offer.in_home(".cursor"), "mcp.json", {"url": CONNECTOR_URL}
    )
    assert cursor.add() is False
    assert cursor_file.read_bytes() == json_bytes(CURSOR_CONFIG)
    assert sorted(p.name for p in cursor_file.parent.iterdir()) == [
        "mcp.json", "mcp.json.ifixai-backup",
    ]


@needs_tomllib
def test_codex_honours_codex_home(
    agent_cli: CommandRecorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "codex-elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(elsewhere))

    assert [agent.name for agent in pro_offer.AGENTS if agent.found()] == ["Codex"]
    assert pro_offer.CodexAgent().add() is True
    assert (elsewhere / "config.toml").read_bytes() == CODEX_TABLE


@needs_tomllib
def test_codex_with_ifixai_already_is_never_changed(
    agent_cli: CommandRecorder, codex_home: Path
) -> None:
    mine = b'[mcp_servers.ifixai]\nurl = "https://my.own/mcp"\n'
    (codex_home / "config.toml").write_bytes(mine)

    assert pro_offer.CodexAgent().add() is True
    assert (codex_home / "config.toml").read_bytes() == mine
    assert not backup_of(codex_home / "config.toml").exists()


@pytest.mark.parametrize(
    "content",
    [b"model = \n", b'mcp_servers = { other = { command = "x" } }\n', b"mcp_servers = 3\n"],
    ids=["broken", "inline-table", "not-a-table"],
)
def test_a_codex_file_that_cannot_take_the_table_is_left_alone(
    person_answering: None, agent_cli: CommandRecorder, codex_home: Path, content: bytes
) -> None:
    (codex_home / "config.toml").write_bytes(content)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert result.exit_code == 0, result.output
    assert (codex_home / "config.toml").read_bytes() == content
    assert after_steps(result.output)[-3:] == [
        "Could not add it to Codex. Run this yourself:", CODEX_BY_HAND, RESTART,
    ]


def test_codex_on_the_path_without_its_folder_gets_the_line_and_no_folder(
    agent_cli: CommandRecorder, home: Path
) -> None:
    agent_cli.installed["codex"] = (0, "", "")

    assert [agent.name for agent in pro_offer.AGENTS if agent.found()] == ["Codex"]
    assert pro_offer.CodexAgent().add() is False
    assert not (home / ".codex").exists()
    assert agent_cli.calls == []


def test_codex_without_tomllib_gets_the_line(
    agent_cli: CommandRecorder, codex_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pro_offer, "tomllib", None)

    assert pro_offer.CodexAgent().add() is False
    assert list(codex_home.iterdir()) == []


OPENCODE_ENTRY = {"type": "remote", "url": CONNECTOR_URL, "enabled": True}
OPENCODE_SNIPPET = (
    '  "ifixai": {"type": "remote", "url": "https://mcp.ifixai.ai/mcp", "enabled": true}'
)
OPENCODE_CONFIG = {
    "$schema": "https://opencode.ai/config.json",
    "model": "anthropic/claude-sonnet-4-5",
    "mcp": {"other": {"type": "local", "command": ["other-server"]}},
}


@pytest.fixture
def opencode_folder(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """opencode's global config folder, at its default place under the home folder."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    folder = home / ".config" / "opencode"
    folder.mkdir(parents=True)
    return folder


def test_one_key_adds_the_server_to_opencode_and_keeps_its_other_settings(
    person_answering: None, agent_cli: CommandRecorder, opencode_folder: Path
) -> None:
    agent_cli.installed["opencode"] = (0, "", "")
    path = opencode_folder / "opencode.json"
    path.write_bytes(json_bytes(OPENCODE_CONFIG))

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert result.exit_code == 0, result.output
    assert after_steps(result.output)[0] == "Found on this computer: opencode."
    assert after_steps(result.output)[-2:] == ["Added to opencode.", RESTART]
    assert agent_cli.calls == []
    assert json.loads(path.read_text(encoding="utf-8")) == {
        **OPENCODE_CONFIG,
        "mcp": {**OPENCODE_CONFIG["mcp"], "ifixai": OPENCODE_ENTRY},
    }
    assert backup_of(path).read_bytes() == json_bytes(OPENCODE_CONFIG)


def test_opencode_without_a_file_gets_a_new_opencode_json(
    agent_cli: CommandRecorder, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME")
    (home / ".config" / "opencode").mkdir(parents=True)

    assert [agent.name for agent in pro_offer.AGENTS if agent.found()] == ["opencode"]
    assert pro_offer.OpencodeAgent().add() is True
    path = home / ".config" / "opencode" / "opencode.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"mcp": {"ifixai": OPENCODE_ENTRY}}
    assert sorted(p.name for p in path.parent.iterdir()) == ["opencode.json"]


def test_opencode_honours_xdg_config_home(
    agent_cli: CommandRecorder, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "xdg-elsewhere"
    (elsewhere / "opencode").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(elsewhere))

    assert pro_offer.OpencodeAgent().add() is True
    assert (elsewhere / "opencode" / "opencode.json").exists()
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("others", [["opencode.json"], ["opencode.json", "config.json"], []])
def test_opencode_writes_the_jsonc_file_it_reads_last_when_that_is_plain_json(
    agent_cli: CommandRecorder, opencode_folder: Path, others: list[str]
) -> None:
    jsonc = opencode_folder / "opencode.jsonc"
    jsonc.write_bytes(b'{\n  "$schema": "https://opencode.ai/config.json"\n}')
    for name in others:
        (opencode_folder / name).write_bytes(b'{"model": "x"}')

    assert pro_offer.OpencodeAgent().add() is True
    assert json.loads(jsonc.read_text(encoding="utf-8")) == {
        "$schema": "https://opencode.ai/config.json", "mcp": {"ifixai": OPENCODE_ENTRY},
    }
    for name in others:
        assert (opencode_folder / name).read_bytes() == b'{"model": "x"}'


@pytest.mark.parametrize(
    "file,content",
    [
        ("opencode.jsonc", b'{\n  // mine\n  "model": "x",\n}\n'),
        ("opencode.json", b"{broken"),
        ("config.json", b"[1]"),
        ("opencode.json", b'{"mcp": ["a"]}'),
    ],
    ids=["jsonc-with-comments", "broken", "not-an-object", "mcp-not-an-object"],
)
def test_an_opencode_file_that_does_not_parse_is_left_alone_with_the_entry_to_paste(
    person_answering: None,
    agent_cli: CommandRecorder,
    opencode_folder: Path,
    file: str,
    content: bytes,
) -> None:
    (opencode_folder / file).write_bytes(content)

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in opencode_folder.iterdir()) == [file]
    assert (opencode_folder / file).read_bytes() == content
    assert after_steps(result.output)[-4:] == [
        'Could not add it to opencode. Add this under "mcp" in',
        f"~/.config/opencode/{file}:",
        OPENCODE_SNIPPET,
        RESTART,
    ]


@pytest.mark.parametrize(
    "file,content",
    [
        ("opencode.json", {"mcp": {"ifixai": {"type": "remote", "url": "https://my.own/mcp"}}}),
        ("opencode.json", {"mcp": {"servers": {"ifixai": {"type": "remote", "url": "x"}}}}),
        ("config.json", {"mcp": {"ifixai": {"type": "remote", "url": "https://my.own/mcp"}}}),
    ],
    ids=["flat", "v2-servers", "in-a-file-merged-before"],
)
def test_an_existing_ifixai_in_any_opencode_file_counts_as_added_and_nothing_changes(
    agent_cli: CommandRecorder, opencode_folder: Path, file: str, content: dict[str, object]
) -> None:
    (opencode_folder / file).write_bytes(json_bytes(content))
    (opencode_folder / "opencode.jsonc").write_bytes(b'{"model": "x"}')
    before = {p.name: p.read_bytes() for p in opencode_folder.iterdir()}

    assert pro_offer.OpencodeAgent().add() is True
    assert {p.name: p.read_bytes() for p in opencode_folder.iterdir()} == before


def test_opencode_on_the_path_without_its_folder_gets_the_entry_to_paste_and_no_folder(
    person_answering: None, agent_cli: CommandRecorder, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    agent_cli.installed["opencode"] = (0, "", "")

    result = CliRunner().invoke(start_offer, input=PRO_ANSWER + ADD_FOR_ME)

    assert result.exit_code == 0, result.output
    assert after_steps(result.output)[-4:] == [
        'Could not add it to opencode. Add this under "mcp" in',
        "~/.config/opencode/opencode.json:",
        OPENCODE_SNIPPET,
        RESTART,
    ]
    assert not (home / ".config" / "opencode").exists()
    assert agent_cli.calls == []


# --- The end of a run -----------------------------------------------------------------------


def test_the_steps_print_on_stderr_after_every_real_run_with_a_verdict(
    terminal: None,
    events: dict[str, EventRecorder],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pro_offer.shutil, "which", fake_command)

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
