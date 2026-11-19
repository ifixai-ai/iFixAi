"""The free Pro audit offer: a choice at the start of a run, and the steps at its end.

The start screen shows once per machine, before anything is sent to an agent, as
a choice between a free Pro audit and carrying on with open source. Picking Pro
ends the run, prints the steps, and offers to add the iFixAi server to every
coding agent found on the machine. The steps screen is also the last thing
printed on every finished real run (not the mock agent, at least one inspection
with a verdict). Neither shows in CI or without a terminal. Skip both with
``--no-promo`` or ``IFIXAI_NO_PROMO=1``.
"""

import json
import os
import shutil
import stat

# Runs the user's own agent CLIs with fixed argument lists, never a shell.
import subprocess  # nosec B404
import sys
import tempfile
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TextIO, TypedDict

import click

from ifixai import telemetry
from ifixai.cli import ui
from ifixai.cli._branding import _ACCENT_RGB, supports_color
from ifixai.core.types import TestRunResult, TestStatus

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: Codex's file is then never written.
    tomllib = None  # type: ignore[assignment]

NO_PROMO_ENV_VAR = "IFIXAI_NO_PROMO"
OFFER_MARKER_NAME = "pro-offer-shown"
MOCK_PROVIDER = "mock"
CONNECTOR_URL = "https://mcp.ifixai.ai/mcp"
PROMO_CODE = "SF2026"
SERVER_NAME = "ifixai"
ADD_TIMEOUT_SECONDS = 60
BACKUP_SUFFIX = ".ifixai-backup"
CODEX_TABLE = f'\n[mcp_servers.{SERVER_NAME}]\nurl = "{CONNECTOR_URL}"\n'
# The flat shape: opencode 1.3 reads only this one, and later versions still accept it.
OPENCODE_ENTRY: dict[str, object] = {"type": "remote", "url": CONNECTOR_URL, "enabled": True}
# opencode merges all three; its own settings writer edits the first that exists.
OPENCODE_FILES = ("opencode.jsonc", "opencode.json", "config.json")
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
ADD_FOR_ME_OPTION = "Add it for me"
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


def agents_question(count: int) -> ChoiceQuestion:
    """The add question, worded for one agent or several."""
    return ChoiceQuestion(
        message=f"Add the iFixAi server to {'it' if count == 1 else 'them'} now?",
        options=(ADD_FOR_ME_OPTION, ADD_MYSELF_OPTION),
        default=ADD_FOR_ME_OPTION,
    )


def offer_at_start(provider: str) -> bool:
    """Make the start offer once per machine; True when the user picked the Pro audit.

    A Pro pick has printed the steps (and offered to add the server to the coding
    agents found), and the caller ends the run before anything is sent to an agent.
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
    offer_agent_setup()
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


def offer_agent_setup() -> None:
    """Name every coding agent found on this computer and offer to add the server to them.

    Each agent is added on its own: a failure prints that agent's manual line after
    the others are done and never stops the rest or the run.
    """
    found = [agent for agent in AGENTS if agent.found()]
    if not found:
        return
    width = terminal_width()
    click.echo()
    names = ", ".join(agent.name for agent in found)
    for line in wrap(("", "", f"Found on this computer: {names}."), width):
        click.echo(line)
    if ask_choice(agents_question(len(found))) != ADD_FOR_ME_OPTION:
        return
    failed: list[CodingAgent] = []
    for agent in found:
        if agent.add():
            click.echo(click.style(f"Added to {agent.name}.", fg="green"))
        else:
            failed.append(agent)
    for agent in failed:
        header, *by_hand = agent.manual_lines()
        for line in wrap(("", "", header), width):
            click.echo(click.style(line, fg="yellow"))
        for line in by_hand:
            click.echo(line)
    for line in wrap(("", "", "Restart your coding agent, then do steps 2 and 3."), width):
        click.echo(line)


class CodingAgent(Protocol):
    @property
    def name(self) -> str: ...

    def found(self) -> bool: ...

    def add(self) -> bool: ...

    def manual_lines(self) -> list[str]: ...


@dataclass(frozen=True)
class CommandAgent:
    """An agent whose own CLI adds the server; found when that CLI is on the PATH."""

    name: str
    command: str
    add_args: tuple[str, ...]

    def found(self) -> bool:
        return shutil.which(self.command) is not None

    def add(self) -> bool:
        """Run the agent's documented add command; "already exists" counts as done."""
        executable = shutil.which(self.command)
        if executable is None:
            return False
        try:
            # A fixed argument list and the user's own CLI from their PATH. stdin is closed
            # so a CLI that prompts can never wait on the person's keys.
            completed = subprocess.run(  # nosec B603
                [executable, *self.add_args],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=ADD_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        answer = f"{completed.stdout}\n{completed.stderr}".lower()
        return completed.returncode == 0 or "already exists" in answer

    def manual_lines(self) -> list[str]:
        return [
            f"Could not add it to {self.name}. Run this yourself:",
            f"  {self.command} {' '.join(self.add_args)}",
        ]


@dataclass(frozen=True)
class CommandFileAgent(CommandAgent):
    """A CLI whose own ``mcp add`` writes the entry, then waits for a browser sign-in past
    the time limit. The entry goes into the user file that command writes instead; the
    person still gets the command when that fails."""

    settings: "JsonFileAgent" = field(compare=False, kw_only=True)

    def add(self) -> bool:
        return self.settings.folder().is_dir() and self.settings.add()


@dataclass(frozen=True)
class JsonFileAgent:
    """An agent with a documented JSON user file; found when its own folder exists."""

    name: str
    folder: Callable[[], Path] = field(compare=False)
    file: str
    entry: dict[str, object] = field(compare=False)
    # Skipped when this CLI is on the PATH: that CLI owns the same file or the same user.
    unless: str | None = None

    def config_path(self) -> Path:
        return self.folder() / self.file

    def found(self) -> bool:
        if self.unless is not None and shutil.which(self.unless) is not None:
            return False
        return self.folder().is_dir()

    def add(self) -> bool:
        try:
            return add_json_entry(self.config_path(), self.entry)
        except (OSError, ValueError):
            return False

    def manual_lines(self) -> list[str]:
        return [
            f'Could not add it to {self.name}. Add this under "mcpServers" in '
            f"{display_path(self.config_path())}:",
            f'  "{SERVER_NAME}": {json.dumps(self.entry)}',
        ]


@dataclass(frozen=True)
class CodexAgent:
    """Codex's ``config.toml``. Its own ``mcp add`` is not run: it replaces an existing
    entry, and for this server it starts a sign-in and waits for it."""

    name: str = "Codex"
    command: str = "codex"

    def home_folder(self) -> Path:
        configured = os.environ.get("CODEX_HOME")
        return Path(configured).expanduser() if configured else Path.home() / ".codex"

    def found(self) -> bool:
        return shutil.which(self.command) is not None or self.home_folder().is_dir()

    def add(self) -> bool:
        folder = self.home_folder()
        if tomllib is None or not folder.is_dir():
            return False
        try:
            return add_codex_table(folder / "config.toml")
        except (OSError, ValueError):
            return False

    def manual_lines(self) -> list[str]:
        return [
            f"Could not add it to {self.name}. Run this yourself:",
            f"  {self.command} mcp add {SERVER_NAME} --url {CONNECTOR_URL}",
        ]


def read_json_object(path: Path) -> dict[str, object] | None:
    """The file's JSON object, ``{}`` when it is missing or empty, None when it is not an object.

    Raises ValueError when it does not parse (comments, a broken file, not UTF-8).
    """
    if not path.exists():
        return {}
    text = path.read_bytes().decode("utf-8")
    if not text.strip():
        return {}
    config = json.loads(text)
    return config if isinstance(config, dict) else None


@dataclass(frozen=True)
class OpencodeAgent:
    """opencode's global config. Its own ``mcp add`` asks its questions in the terminal
    (1.3.13 has no ``--url``), so the entry goes into the file opencode reads."""

    name: str = "opencode"
    command: str = "opencode"

    def config_folder(self) -> Path:
        return xdg_config_home() / "opencode"

    def config_path(self) -> Path:
        """The file opencode's own settings writer edits: the first one that exists."""
        folder = self.config_folder()
        existing = (folder / name for name in OPENCODE_FILES if (folder / name).exists())
        return next(existing, folder / "opencode.json")

    def found(self) -> bool:
        return shutil.which(self.command) is not None or self.config_folder().is_dir()

    def add(self) -> bool:
        """Add the entry unless one of the files opencode merges already names the server."""
        folder = self.config_folder()
        if not folder.is_dir():
            return False
        try:
            configs = [read_json_object(folder / name) for name in OPENCODE_FILES]
            if any(config is None for config in configs):
                return False
            if any(has_opencode_server(config) for config in configs if config is not None):
                return True
            return add_json_entry(self.config_path(), OPENCODE_ENTRY, key="mcp")
        except (OSError, ValueError):
            return False

    def manual_lines(self) -> list[str]:
        return [
            f'Could not add it to {self.name}. Add this under "mcp" in '
            f"{display_path(self.config_path())}:",
            f'  "{SERVER_NAME}": {json.dumps(OPENCODE_ENTRY)}',
        ]


def has_opencode_server(config: dict[str, object]) -> bool:
    """True when the server is under ``mcp``, or under ``mcp.servers`` (opencode's V2 shape)."""
    servers = config.get("mcp")
    if not isinstance(servers, dict):
        return False
    nested = servers.get("servers")
    return SERVER_NAME in servers or (isinstance(nested, dict) and SERVER_NAME in nested)


def add_json_entry(path: Path, entry: dict[str, object], key: str = "mcpServers") -> bool:
    """Add the server under ``key``, keeping every other key; True when it is there.

    A file that does not parse as a JSON object (comments, a broken file) is left alone.
    An existing ``ifixai`` entry counts as done and the file is not touched.
    """
    target = path.resolve()
    config = read_json_object(target)
    if config is None:
        return False
    servers = config.setdefault(key, {})
    if not isinstance(servers, dict):
        return False
    if SERVER_NAME in servers:
        return True
    servers[SERVER_NAME] = entry
    if not target.parent.is_dir():
        # Only a subfolder inside the agent's own folder, which is known to exist.
        target.parent.mkdir()
    text = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    replace_file(target, text.encode("utf-8"))
    return True


def add_codex_table(path: Path) -> bool:
    """Append the server's table to Codex's ``config.toml``, keeping every existing byte.

    A file that does not parse is left alone. The result is parsed again and must equal
    the old settings plus the server, or nothing is written (for example when
    ``mcp_servers`` is an inline table, which a later table cannot extend).
    """
    if tomllib is None:
        return False
    target = path.resolve()
    old_bytes = target.read_bytes() if target.exists() else b""
    old = tomllib.loads(old_bytes.decode("utf-8"))
    servers = old.get("mcp_servers", {})
    if not isinstance(servers, dict):
        return False
    if SERVER_NAME in servers:
        return True
    new_bytes = old_bytes + CODEX_TABLE.encode("utf-8")
    expected = {**old, "mcp_servers": {**servers, SERVER_NAME: {"url": CONNECTOR_URL}}}
    if tomllib.loads(new_bytes.decode("utf-8")) != expected:
        return False
    replace_file(target, new_bytes)
    return True


def replace_file(target: Path, data: bytes) -> None:
    """Back up the current file, then swap the new bytes in with one rename.

    The temporary file sits in the same folder, so the rename is atomic, and takes the
    old file's permissions; a reader never sees a half-written file.
    """
    mode = None
    if target.exists():
        mode = stat.S_IMODE(target.stat().st_mode)
        shutil.copy2(target, target.with_name(target.name + BACKUP_SUFFIX))
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            Path(temporary).chmod(mode)
        Path(temporary).replace(target)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def display_path(path: Path) -> str:
    """The path as a person types it, with the home folder as ``~``."""
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def in_home(folder: str) -> Callable[[], Path]:
    return lambda: Path.home() / folder


def xdg_config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    return Path(configured) if configured else Path.home() / ".config"


def copilot_home() -> Path:
    """GitHub Copilot's user folder: ``COPILOT_HOME`` replaces ``~/.copilot`` when set."""
    configured = os.environ.get("COPILOT_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".copilot"


def devin_config_folder() -> Path:
    """Devin's user folder, shared by the CLI and Devin Desktop: ``%APPDATA%\\devin`` on
    Windows, else ``devin`` in the XDG config folder (the CLI follows ``XDG_CONFIG_HOME``)."""
    appdata = os.environ.get("APPDATA")
    if sys.platform == "win32" and appdata:
        return Path(appdata) / "devin"
    return xdg_config_home() / "devin"


def http_add(*scope: str) -> tuple[str, ...]:
    return ("mcp", "add", "--transport", "http", *scope, SERVER_NAME, CONNECTOR_URL)


# Written by Devin Desktop and by the Devin CLI's ``mcp add -s user``.
DEVIN_SETTINGS = JsonFileAgent(
    "Devin Desktop", devin_config_folder, "mcp_config.json",
    {"url": CONNECTOR_URL, "transport": "http"}, unless="devin",
)

# Every agent the server is added to, in the order the question names them.
AGENTS: tuple[CodingAgent, ...] = (
    CommandAgent("Claude Code", "claude", http_add("--scope", "user")),
    CodexAgent(),
    CommandAgent("Gemini CLI", "gemini", http_add("--scope", "user")),
    CommandAgent("GitHub Copilot CLI", "copilot", http_add()),
    CommandFileAgent(
        "Devin CLI", "devin", ("mcp", "add", "-s", "user", SERVER_NAME, CONNECTOR_URL),
        settings=DEVIN_SETTINGS,
    ),
    CommandAgent(
        "Cline CLI", "cline",
        ("mcp", "add", SERVER_NAME, CONNECTOR_URL, "--transport", "http", "--yes"),
    ),
    CommandAgent("Amp", "amp", ("mcp", "add", SERVER_NAME, CONNECTOR_URL)),
    OpencodeAgent(),
    JsonFileAgent("Cursor", in_home(".cursor"), "mcp.json", {"url": CONNECTOR_URL}),
    JsonFileAgent(
        "VS Code Copilot", copilot_home, "mcp-config.json",
        {"type": "http", "url": CONNECTOR_URL, "tools": ["*"]}, unless="copilot",
    ),
    JsonFileAgent("Kiro", in_home(".kiro"), "settings/mcp.json", {"url": CONNECTOR_URL}),
    JsonFileAgent("Junie", in_home(".junie"), "mcp/mcp.json", {"url": CONNECTOR_URL}),
    JsonFileAgent("Warp", in_home(".warp"), ".mcp.json", {"url": CONNECTOR_URL}),
    JsonFileAgent(
        "Antigravity CLI", in_home(".gemini/config"), "mcp_config.json",
        {"serverUrl": CONNECTOR_URL},
    ),
    DEVIN_SETTINGS,
)

def render_screen(
    headline: tuple[str, ...], body: tuple[Paragraph, ...], stream: TextIO | None
) -> str:
    """The screen as text, wrapped at word boundaries to the terminal, never mid-word.

    The URL and the code are single words, so they are never broken; they are
    coloured only after wrapping, so colour codes never count toward a line's width.
    """
    width = terminal_width()
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


def terminal_width() -> int:
    return max(shutil.get_terminal_size().columns - 1, MIN_WRAP_WIDTH)


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
