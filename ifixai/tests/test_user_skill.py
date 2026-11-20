"""The first ifixai command gives the user's coding agent the iFixAi skill, once, quietly."""

import os
import stat
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ifixai import telemetry
from ifixai.cli import main as cli_main
from ifixai.cli import scaffold, user_skill
from ifixai.cli.main import ifixai_cli

ENV_VARS = (
    "XDG_CONFIG_HOME", "CODEX_HOME", "COPILOT_HOME", "CLAUDE_CONFIG_DIR", "APPDATA",
    user_skill.NO_SKILL_ENV_VAR, "CI", *telemetry._CI_VENDOR_VARS,
    "AI_AGENT", "OPENCODE", "CURSOR_EXTENSION_HOST_ROLE",
    *(name for _, names in user_skill.RUNNING_AGENT_VARS for name in names),
)
ADDED = 'iFixAi skill added to your coding agent: ask it to "run iFixAi on my agent". Undo: ifixai install --revert\n'



@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty home with no agent CLI on the PATH, outside CI."""
    home = tmp_path / "home"
    home.mkdir()
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(empty_bin))
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(user_skill, "DEVIN_MARKER", tmp_path / "no-devin")
    monkeypatch.chdir(tmp_path)
    return home


def run(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, *args: str) -> str:
    """Run ``ifixai <args>`` through the real entry point; return what it wrote to stderr."""
    monkeypatch.setattr(sys, "argv", ["ifixai", *args])
    with pytest.raises(SystemExit) as exited:
        cli_main.main()
    assert exited.value.code in (0, None)
    return capsys.readouterr().err


def current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def skill(folder: Path) -> Path:
    return folder / "ifixai" / "SKILL.md"


def frontmatter(path: Path) -> dict[str, str]:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])


def written(home: Path) -> list[str]:
    return sorted(str(p.relative_to(home)) for p in home.rglob("SKILL.md"))


def test_from_a_terminal_with_claude_code_it_writes_two_files_in_one_line(
    home, capsys, monkeypatch
):
    (home / ".claude").mkdir()

    assert run(capsys, monkeypatch, "--help") == ADDED

    assert written(home) == [".agents/skills/ifixai/SKILL.md", ".claude/skills/ifixai/SKILL.md"]
    for path in (skill(home / ".claude" / "skills"), skill(home / ".agents" / "skills")):
        assert frontmatter(path)["name"] == "ifixai"
        assert "Use when the user asks to audit" in frontmatter(path)["description"]
    mode = stat.S_IMODE(skill(home / ".claude" / "skills").stat().st_mode)
    assert mode == 0o666 & ~current_umask()


def test_from_a_terminal_without_claude_code_it_writes_only_the_shared_file(
    home, capsys, monkeypatch
):
    (home / ".codex").mkdir()
    (home / ".cline").mkdir()

    assert run(capsys, monkeypatch, "--help") == ADDED
    assert written(home) == [".agents/skills/ifixai/SKILL.md"]


@pytest.mark.parametrize(
    "env,folder",
    [
        (("CURSOR_AGENT", "1"), ".agents/skills"),
        (("CLAUDECODE", "1"), ".claude/skills"),
        (("AI_AGENT", "claude-code_2-1-326_agent"), ".claude/skills"),
        (("AI_AGENT", "amp"), ".agents/skills"),
        (("CODEX_THREAD_ID", "t1"), ".agents/skills"),
        (("OPENCODE", "1"), ".agents/skills"),
        (("ANTIGRAVITY_AGENT", "1"), ".gemini/config/skills"),
    ],
)
def test_run_by_a_coding_agent_it_writes_only_that_agents_folder(
    home, capsys, monkeypatch, env, folder
):
    (home / ".claude").mkdir()
    monkeypatch.setenv(*env)

    assert run(capsys, monkeypatch, "--help") == ADDED
    assert written(home) == [f"{folder}/ifixai/SKILL.md"]


def test_the_generic_variable_wins_over_an_agents_own(home, capsys, monkeypatch):
    monkeypatch.setenv("AI_AGENT", "cursor")
    monkeypatch.setenv("CLAUDECODE", "1")

    run(capsys, monkeypatch, "--help")
    assert written(home) == [".agents/skills/ifixai/SKILL.md"]


def test_the_second_command_says_and_writes_nothing(home, capsys, monkeypatch):
    (home / ".claude").mkdir()
    run(capsys, monkeypatch, "--help")
    path = skill(home / ".claude" / "skills")
    before = path.stat().st_mtime_ns

    assert run(capsys, monkeypatch, "--help") == ""
    assert path.stat().st_mtime_ns == before


def test_a_new_version_refreshes_every_copy_and_says_so(home, capsys, monkeypatch):
    (home / ".claude").mkdir()
    run(capsys, monkeypatch, "--help")
    monkeypatch.setattr(user_skill, "VERSION", "99.0.0")
    monkeypatch.setattr(scaffold, "load_playbook", lambda: "# New instructions")
    monkeypatch.setenv("CLAUDECODE", "1")

    err = run(capsys, monkeypatch, "--help")

    assert err == "iFixAi skill updated to 99.0.0. Undo: ifixai install --revert\n"
    for folder in (".claude", ".agents"):
        text = skill(home / folder / "skills").read_text(encoding="utf-8")
        assert "# New instructions" in text


def test_a_new_version_with_the_same_text_says_nothing(home, capsys, monkeypatch):
    (home / ".claude").mkdir()
    run(capsys, monkeypatch, "--help")
    monkeypatch.setattr(user_skill, "VERSION", "99.0.0")

    assert run(capsys, monkeypatch, "--help") == ""


def test_an_agent_without_the_file_gets_it_when_it_runs_ifixai(home, capsys, monkeypatch):
    run(capsys, monkeypatch, "--help")
    assert written(home) == [".agents/skills/ifixai/SKILL.md"]
    monkeypatch.setenv("ANTIGRAVITY_AGENT", "1")

    assert run(capsys, monkeypatch, "--help") == ADDED
    assert skill(home / ".gemini" / "config" / "skills").is_file()
    assert run(capsys, monkeypatch, "--help") == ""


def test_an_agent_reading_a_folder_already_written_adds_no_copy():
    claude, codex, opencode = (
        next(a for a in user_skill.AGENTS if a.name == name)
        for name in ("Claude Code", "Codex", "opencode")
    )
    folders = user_skill.plan([opencode, codex, claude])

    assert folders == {
        Path.home() / ".claude" / "skills": ["Claude Code"],
        Path.home() / ".agents" / "skills": ["Codex", "opencode"],
    }
    assert user_skill.plan([opencode]) == {Path.home() / ".agents" / "skills": ["opencode"]}


def test_a_skill_the_user_wrote_is_left_alone_with_how_to_replace_it(home, capsys, monkeypatch):
    own = skill(home / ".claude" / "skills")
    own.parent.mkdir(parents=True)
    own.write_text("my own skill\n", encoding="utf-8")

    err = run(capsys, monkeypatch, "--help")

    assert err.count("\n") == 1
    assert err.startswith("iFixAi skill added to your coding agent")
    assert "left your own ~/.claude/skills/ifixai/SKILL.md alone" in err
    assert "ifixai install --user --force" in err
    assert own.read_text(encoding="utf-8") == "my own skill\n"

    result = CliRunner().invoke(ifixai_cli, ["install", "--user", "--force"])
    assert result.exit_code == 0, result.output
    assert scaffold.MD_BEGIN in own.read_text(encoding="utf-8")


def test_revert_removes_our_skills_keeps_the_users_and_stays_off(home, capsys, monkeypatch, tmp_path):
    (home / ".codex").mkdir()
    own = skill(home / ".claude" / "skills")
    own.parent.mkdir(parents=True)
    own.write_text("my own skill\n", encoding="utf-8")
    legacy = home / ".codex" / "prompts" / "ifixai-skill.md"
    legacy.parent.mkdir()
    legacy.write_text(scaffold.MD_BEGIN + "\nold\n" + scaffold.MD_END + "\n", encoding="utf-8")
    run(capsys, monkeypatch, "--help")
    ours = skill(home / ".agents" / "skills")
    assert ours.is_file()
    monkeypatch.setenv("ANTIGRAVITY_AGENT", "1")
    run(capsys, monkeypatch, "--help")
    monkeypatch.delenv("ANTIGRAVITY_AGENT")
    antigravity = skill(home / ".gemini" / "config" / "skills")
    assert antigravity.is_file()

    assert run(capsys, monkeypatch, "install", "--revert", "--dir", str(tmp_path)) == ""

    assert not ours.parent.exists()
    assert not antigravity.exists()
    assert not legacy.exists()
    assert own.read_text(encoding="utf-8") == "my own skill\n"
    assert user_skill.read_state() == {"opted_out": True}
    assert run(capsys, monkeypatch, "--help") == ""
    assert not ours.exists()

    result = CliRunner().invoke(ifixai_cli, ["install", "--user"])
    assert result.exit_code == 0, result.output
    assert ours.is_file()
    assert user_skill.read_state() == {"version": user_skill.VERSION}


@pytest.mark.parametrize("env", [("CI", "true"), (user_skill.NO_SKILL_ENV_VAR, "1")])
def test_ci_and_no_skill_write_and_print_nothing(home, capsys, monkeypatch, env):
    (home / ".claude").mkdir()
    monkeypatch.setenv(*env)

    assert run(capsys, monkeypatch, "--help") == ""
    assert not (home / ".claude" / "skills").exists()
    assert not user_skill.state_path().exists()


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_a_read_only_folder_costs_one_quiet_line_and_the_command_still_runs(
    home, capsys, monkeypatch
):
    claude = home / ".claude"
    claude.mkdir()
    claude.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        err = run(capsys, monkeypatch, "--help")
        assert err.count("\n") == 1
        assert "could not write ~/.claude/skills/ifixai/SKILL.md" in err
        assert run(capsys, monkeypatch, "--help") == ""
    finally:
        claude.chmod(stat.S_IRWXU)


def test_any_error_is_one_line_and_never_stops_the_command(home, capsys, monkeypatch):
    def broken(*_: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(user_skill, "read_state", broken)

    assert run(capsys, monkeypatch, "--help") == "iFixAi skill not installed: disk on fire\n"


def test_codex_install_writes_the_same_skill_as_the_first_command(home, capsys, monkeypatch, tmp_path):
    result = CliRunner().invoke(
        ifixai_cli, ["install", "--agents", "codex", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    path = skill(home / ".agents" / "skills")

    assert path.read_text(encoding="utf-8") == scaffold.render_skill(scaffold.load_playbook())


def test_the_skill_body_is_the_playbook_unchanged():
    playbook = scaffold.PLAYBOOK_PATH.read_text(encoding="utf-8").strip()
    rendered = scaffold.render_skill(scaffold.load_playbook())

    assert rendered.split(scaffold.MD_BEGIN + "\n", 1)[1] == playbook + "\n" + scaffold.MD_END + "\n"
