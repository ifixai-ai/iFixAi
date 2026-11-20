"""A stale installer backup must not hide loss of a newer custom command."""

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli


def _install(root, agent, *extra):
    return CliRunner().invoke(ifixai_cli, ["install", "--agents", agent, "--dir", str(root), *extra])


@pytest.mark.parametrize("agent,relative", [("claude", ".claude/commands/ifixai.md"), ("gemini", ".gemini/commands/ifixai.toml")])
def test_reinstall_preserves_new_custom_command_when_backup_is_stale(tmp_path, agent, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    original = "Original custom instructions.\n"
    newest = "New organization-specific audit instructions.\n"
    path.write_text(original)
    assert _install(tmp_path, agent).exit_code == 0
    backup = path.with_suffix(path.suffix + ".bak")
    assert backup.read_text() == original
    # The user replaces the generated command between installs. Its old backup
    # still exists, but cannot recover this new custom command if overwritten.
    path.write_text(newest)
    result = _install(tmp_path, agent)
    assert result.exit_code != 0, result.output
    assert "backup" in result.output.lower()
    assert path.read_text() == newest
    assert backup.read_text() == original


@pytest.mark.parametrize("preexisting_backup", [False, True])
def test_matching_backup_and_managed_reinstall_remain_revertible(tmp_path, preexisting_backup):
    path = tmp_path / ".claude/commands/ifixai.md"
    path.parent.mkdir(parents=True)
    original = "Custom audit instructions.\n"
    path.write_text(original)
    backup = path.with_suffix(".md.bak")
    if preexisting_backup:
        backup.write_text(original)
    assert _install(tmp_path, "claude").exit_code == 0
    first = path.read_text()
    assert _install(tmp_path, "claude").exit_code == 0
    assert path.read_text() == first
    assert backup.read_text() == original
    assert _install(tmp_path, "claude", "--revert").exit_code == 0
    assert path.read_text() == original
    assert not backup.exists()


def test_explicit_force_retains_its_overwrite_behavior(tmp_path):
    path = tmp_path / ".claude/commands/ifixai.md"
    path.parent.mkdir(parents=True)
    path.write_text("Current custom command.\n")
    backup = path.with_suffix(".md.bak")
    backup.write_text("Older command.\n")
    result = _install(tmp_path, "claude", "--force")
    assert result.exit_code == 0, result.output
    assert "managed by `ifixai install`" in path.read_text()
    assert backup.read_text() == "Older command.\n"
