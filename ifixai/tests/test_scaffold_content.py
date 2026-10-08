"""The public installer must retain unmanaged AGENTS.md text."""

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli
from ifixai.cli.scaffold import MD_BEGIN, MD_END


def _install(tmp_path, *extra):
    result = CliRunner().invoke(
        ifixai_cli, ["install", "--agents", "agents", "--dir", str(tmp_path), *extra]
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_install_keeps_existing_user_text(tmp_path, newline):
    path = tmp_path / "AGENTS.md"
    original = ("  # Owned instructions" + newline + newline
                + "Keep this Markdown hard break.  " + newline * 3)
    path.write_bytes(original.encode())
    _install(tmp_path)
    assert path.read_bytes().startswith(original.encode())


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("revert", [False, True])
def test_replace_or_revert_keeps_unmanaged_prefix_and_suffix(tmp_path, newline, revert):
    path = tmp_path / "AGENTS.md"
    prefix = "  # Owned instructions  " + newline * 3
    suffix = newline + '    echo "this remains indented code"' + newline * 2
    path.write_bytes((prefix + MD_BEGIN + "\nold body\n" + MD_END + "\n" + suffix).encode())
    _install(tmp_path, *(["--revert"] if revert else []))
    contents = path.read_bytes()
    assert contents.startswith(prefix.encode())
    assert contents.endswith(suffix.encode())
    if revert:
        assert contents == (prefix + suffix).encode()
    else:
        assert "old body" not in contents.decode()
        assert contents.count(MD_BEGIN.encode()) == 1


def test_install_is_idempotent_and_revert_removes_generated_file(tmp_path):
    path = tmp_path / "AGENTS.md"
    _install(tmp_path)
    first = path.read_bytes()
    _install(tmp_path)
    assert path.read_bytes() == first
    _install(tmp_path, "--revert")
    assert not path.exists()


def test_unbalanced_managed_marker_keeps_owned_file(tmp_path):
    path = tmp_path / "AGENTS.md"
    original = "# Owned\n" + MD_BEGIN + "\nIncomplete\n"
    path.write_text(original, encoding="utf-8")
    result = CliRunner().invoke(
        ifixai_cli, ["install", "--agents", "agents", "--dir", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert path.read_text(encoding="utf-8") == original
