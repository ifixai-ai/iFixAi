"""The iFixAi skill in every coding agent on this computer, from the first ``ifixai`` command.

pip runs no code when it installs a wheel, so the first ``ifixai`` command of any kind writes
``ifixai/SKILL.md`` (Agent Skills format: ``name`` and ``description`` frontmatter, loaded by
description) into the user-level skills folder of every coding agent found. It runs again when
iFixAi is upgraded or a new agent appears, prints one line on stderr when something changed,
and never fails the command. Never in CI or with ``IFIXAI_NO_SKILL``. ``ifixai install
--revert`` removes it and keeps it off; ``ifixai install --user`` puts it back.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click

from ifixai import telemetry
from ifixai._version import VERSION

# scaffold imports this module for `ifixai install`, so its names are read at call time.
from ifixai.cli import pro_offer, scaffold

NO_SKILL_ENV_VAR = "IFIXAI_NO_SKILL"
STATE_FILE = "skill.json"
UNDO = "ifixai install --revert"
REPLACE = "ifixai install --user --force"
# The old Codex custom-prompt files; only ever removed, and only with our marker.
LEGACY_CODEX_PROMPTS = ("ifixai-skill.md", "ifixai.md")

Folder = Callable[[], Path]


def _home(*parts: str) -> Folder:
    return lambda: Path.home().joinpath(*parts)


def _xdg(*parts: str) -> Folder:
    return lambda: pro_offer.xdg_config_home().joinpath(*parts)


def _claude_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


# Each agent's user-level skills folders, from its own docs (see docs/get-started.md).
CLAUDE = lambda: _claude_home() / "skills"  # noqa: E731
SHARED = _home(".agents", "skills")
COPILOT = lambda: pro_offer.copilot_home() / "skills"  # noqa: E731
CLINE = _home(".cline", "skills")
KIRO = _home(".kiro", "skills")
ANTIGRAVITY = _home(".gemini", "config", "skills")
GEMINI = _home(".gemini", "skills")
OPENCODE = _xdg("opencode", "skills")
AMP_SHARED = _xdg("agents", "skills")
AMP = _xdg("amp", "skills")
CURSOR = _home(".cursor", "skills")
DEVIN = lambda: pro_offer.devin_config_folder() / "skills"  # noqa: E731
JUNIE = _home(".junie", "skills")
WARP = _home(".warp", "skills")
WINDSURF = _home(".codeium", "windsurf", "skills")

_OFFER_AGENTS = {agent.name: agent for agent in pro_offer.AGENTS}


def _offer_found(name: str) -> Callable[[], bool]:
    """The detection the server offer uses: the agent's CLI on the PATH or its own folder."""
    return _OFFER_AGENTS[name].found


def _folder_found(folder: Folder) -> Callable[[], bool]:
    return lambda: folder().is_dir()


def _either(*checks: Callable[[], bool]) -> Callable[[], bool]:
    return lambda: any(check() for check in checks)


@dataclass(frozen=True)
class SkillAgent:
    """A coding agent that reads Agent Skills from ``reads``; the first is where we write."""

    name: str
    found: Callable[[], bool]
    reads: tuple[Folder, ...]


AGENTS: tuple[SkillAgent, ...] = (
    SkillAgent(
        "Claude Code",
        _either(_offer_found("Claude Code"), _folder_found(_claude_home)),
        (CLAUDE,),
    ),
    SkillAgent("Codex", _offer_found("Codex"), (SHARED,)),
    SkillAgent("Gemini CLI", _offer_found("Gemini CLI"), (SHARED, GEMINI)),
    SkillAgent("GitHub Copilot CLI", _offer_found("GitHub Copilot CLI"), (SHARED, COPILOT)),
    SkillAgent("Devin CLI", _offer_found("Devin CLI"), (SHARED, DEVIN)),
    SkillAgent(
        "Cline", _either(_offer_found("Cline CLI"), _folder_found(_home(".cline"))), (CLINE,)
    ),
    SkillAgent(
        "Amp",
        _either(_offer_found("Amp"), _folder_found(_xdg("amp"))),
        (SHARED, AMP_SHARED, AMP, CLAUDE),
    ),
    SkillAgent("opencode", _offer_found("opencode"), (SHARED, OPENCODE, CLAUDE)),
    SkillAgent("Cursor", _offer_found("Cursor"), (SHARED, CURSOR, CLAUDE)),
    SkillAgent("VS Code Copilot", _offer_found("VS Code Copilot"), (COPILOT,)),
    SkillAgent("Kiro", _offer_found("Kiro"), (KIRO,)),
    SkillAgent("Junie", _offer_found("Junie"), (SHARED, JUNIE)),
    SkillAgent("Warp", _offer_found("Warp"), (SHARED, WARP)),
    SkillAgent("Antigravity", _offer_found("Antigravity CLI"), (ANTIGRAVITY, GEMINI)),
    SkillAgent("Devin Desktop", _offer_found("Devin Desktop"), (SHARED, DEVIN)),
    SkillAgent(
        "Windsurf", _folder_found(_home(".codeium", "windsurf")), (SHARED, DEVIN, WINDSURF)
    ),
    SkillAgent("Zed", _folder_found(_xdg("zed")), (SHARED,)),
)


def every_folder() -> list[Path]:
    """Every skills folder any agent reads, once each: what --revert looks through."""
    return list(dict.fromkeys(read() for agent in AGENTS for read in agent.reads))


def plan(found: list[SkillAgent]) -> dict[Path, list[str]]:
    """The fewest folders that give every found agent the skill once: folder -> agent names.

    An agent that reads a folder already chosen adds none, so an agent reading several of
    them sees as few copies as possible. Agents with one folder go first, since they can't
    be served any other way.
    """
    chosen: dict[Path, list[str]] = {}
    for agent in sorted(found, key=lambda agent: len(agent.reads)):
        folders = [read() for read in agent.reads]
        folder = next((folder for folder in folders if folder in chosen), folders[0])
        chosen.setdefault(folder, []).append(agent.name)
    return chosen


@dataclass
class Outcome:
    added: list[str]
    updated: list[str]
    skipped: list[Path]
    failed: list[tuple[Path, str]]


def install_skill(
    found: list[SkillAgent], known: frozenset[str] = frozenset(), force: bool = False
) -> tuple[Outcome, dict[Path, list[str]]]:
    """Write the skill for every found agent; ``known`` are the agents that had it before."""
    content = scaffold.render_skill(scaffold.load_playbook())
    outcome = Outcome([], [], [], [])
    folders = plan(found)
    for folder, names in folders.items():
        path = folder / scaffold.SKILL_NAME / scaffold.SKILL_FILE
        try:
            existing = path.read_text(encoding="utf-8") if path.is_file() else None
            if existing is not None and scaffold.MD_BEGIN not in existing and not force:
                outcome.skipped.append(path)
                continue
            if existing != content:
                path.parent.mkdir(parents=True, exist_ok=True)
                _write(path, content)
        except OSError as error:
            outcome.failed.append((path, error.strerror or str(error)))
            continue
        changed = existing != content
        outcome.added += [name for name in names if name not in known]
        outcome.updated += [name for name in names if name in known and changed]
    return outcome, folders


def _write(path: Path, content: str) -> None:
    """Swap the new file in with one rename, so an agent never reads half a skill.

    The temporary file is opened like any new file, so it gets the user's usual permissions.
    """
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def remove_skills() -> list[Path]:
    """Remove every skill file we wrote, in any agent's folder; files without our marker stay."""
    candidates = [folder / scaffold.SKILL_NAME / scaffold.SKILL_FILE for folder in every_folder()]
    candidates += [Path.home() / ".codex" / "prompts" / name for name in LEGACY_CODEX_PROMPTS]
    removed = []
    for path in candidates:
        if not path.is_file() or scaffold.MD_BEGIN not in path.read_text(encoding="utf-8"):
            continue
        path.unlink()
        removed.append(path)
        if path.name == scaffold.SKILL_FILE and not any(path.parent.iterdir()):
            path.parent.rmdir()
    return removed


def state_path() -> Path:
    return telemetry._config_dir() / STATE_FILE


def read_state() -> dict[str, object]:
    """The saved state; a missing or broken file reads as never installed."""
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def write_state(state: dict[str, object]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def skill_disabled() -> bool:
    value = os.environ.get(NO_SKILL_ENV_VAR, "").strip().lower()
    return value not in {"", "0", "false", "f", "no", "n", "off"}


def summary(outcome: Outcome) -> str | None:
    """One line for what changed, or None when nothing did."""
    parts = []
    if outcome.added:
        parts.append(
            f"iFixAi skill added to your coding agents ({', '.join(outcome.added)}): "
            'ask your agent to "run iFixAi on my agent"'
        )
    if outcome.updated:
        parts.append(
            f"iFixAi skill updated to {VERSION} in your coding agents "
            f"({', '.join(outcome.updated)})"
        )
    for path in outcome.skipped:
        parts.append(
            f"left your own {pro_offer.display_path(path)} alone (to replace it: {REPLACE})"
        )
    for path, reason in outcome.failed:
        parts.append(f"could not write {pro_offer.display_path(path)} ({reason})")
    if not parts:
        return None
    line = "; ".join(parts)
    if outcome.added or outcome.updated:
        return f"{line}. Undo: {UNDO}"
    return f"iFixAi skill: {line}"


def ensure_user_skill() -> None:
    """Install or refresh the skill before any command; never stops the command itself.

    Runs only when iFixAi's version changed or an agent appeared since the last time, so
    an unchanged machine costs a few PATH lookups and folder checks.
    """
    if telemetry.in_ci() or skill_disabled():
        return
    try:
        state = read_state()
        if state.get("opted_out"):
            return
        found = [agent for agent in AGENTS if agent.found()]
        known = state.get("agents") if state.get("version") == VERSION else None
        names = frozenset(agent.name for agent in found)
        if isinstance(known, list) and names <= set(known):
            return
        before = state.get("agents")
        outcome, _ = install_skill(found, frozenset(before if isinstance(before, list) else ()))
        # Recorded even after a failure, so a read-only folder costs one line per version.
        write_state({"version": VERSION, "agents": sorted(names)})
        line = summary(outcome)
        if line:
            click.echo(click.style(line, dim=not (outcome.added or outcome.updated)), err=True)
    # The user's own command matters more than the skill: any failure here is one line.
    except Exception as error:  # noqa: BLE001
        click.echo(click.style(f"iFixAi skill not installed: {error}", dim=True), err=True)


def install_now(force: bool) -> None:
    """``ifixai install --user``: install for every agent found now, and turn auto-install on."""
    found = [agent for agent in AGENTS if agent.found()]
    if not found:
        click.echo("No coding agent found on this computer.")
        return
    outcome, folders = install_skill(found, force=force)
    write_state({"version": VERSION, "agents": sorted(agent.name for agent in found)})
    failed = dict(outcome.failed)
    for folder, names in folders.items():
        path = folder / scaffold.SKILL_NAME / scaffold.SKILL_FILE
        shown = pro_offer.display_path(path)
        if path in outcome.skipped:
            note = "not written by iFixAi; --force replaces it"
            click.echo(click.style(f"  skipped {shown} ({note})", fg="yellow"))
        elif path in failed:
            click.echo(click.style(f"  could not write {shown} ({failed[path]})", fg="red"))
        else:
            click.echo(f"  {click.style('wrote', fg='green')} {', '.join(names)}: {shown}")
    click.echo('Ask your agent to "run iFixAi on my agent".')


def revert_user_skills() -> None:
    """Remove every skill we wrote and keep auto-install off until ``ifixai install --user``."""
    for path in remove_skills():
        click.echo(f"  {click.style('removed', fg='yellow')} {pro_offer.display_path(path)}")
    write_state({"opted_out": True})
    click.echo("  The iFixAi skill stays off. To add it again: ifixai install --user")
