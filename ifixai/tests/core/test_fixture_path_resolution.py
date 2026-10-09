from pathlib import Path

import pytest

from ifixai import api
from ifixai.core.fixture_loader import (
    load_fixture,
    resolve_fixture_path,
    validate_fixture,
)


@pytest.mark.asyncio
async def test_public_api_default_fixture_ignores_unrelated_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "default").mkdir()

    result = await api.run_selected({"B02"}, "mock")

    assert [result.test_id for result in result.test_results] == ["B02"]


@pytest.mark.parametrize("name", ["default", "healthcare"])
def test_named_fixture_ignores_matching_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    expected_path = resolve_fixture_path(name)
    expected = load_fixture(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / name).mkdir()

    assert resolve_fixture_path(name) == expected_path
    assert load_fixture(name) == expected
    assert validate_fixture(name) == []


@pytest.mark.parametrize("filename", ["default", "custom.yaml"])
def test_local_fixture_file_keeps_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    contents = resolve_fixture_path("default").read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    path = tmp_path / filename
    path.write_text(contents, encoding="utf-8")

    assert resolve_fixture_path(filename) == Path(filename)
    assert load_fixture(filename).metadata.name == load_fixture(path).metadata.name


def test_nonfixture_directory_is_still_rejected(tmp_path: Path) -> None:
    errors = validate_fixture(tmp_path)

    assert len(errors) == 1
    with pytest.raises((FileNotFoundError, IsADirectoryError)):
        load_fixture(tmp_path)


@pytest.mark.parametrize("explicit", ["./healthcare", "healthcare/", "./default"])
def test_explicit_directory_never_selects_builtin(tmp_path, monkeypatch, explicit):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / explicit
    path.mkdir()
    (path / "fixture.yaml").write_text("broken: [", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        resolve_fixture_path(explicit)
    assert validate_fixture(explicit)


@pytest.mark.skipif(not Path("/dev/stdin").exists(), reason="POSIX stdin device required")
def test_actual_cli_validates_piped_stdin(tmp_path):
    import os
    import subprocess
    import sys

    fixture = resolve_fixture_path("healthcare").read_text(encoding="utf-8")
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), IFIXAI_TELEMETRY="0", DO_NOT_TRACK="1")
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).parents[3]), env.get("PYTHONPATH", "")])
    result = subprocess.run(
        [sys.executable, "-m", "ifixai.cli.main", "validate", "/dev/stdin"],
        input=fixture, cwd=tmp_path, env=env, capture_output=True, text=True,
        check=False, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Valid" in result.stdout


def test_parent_directory_is_not_a_builtin_name(tmp_path, monkeypatch):
    fixtures = tmp_path / "shipped"
    fixtures.mkdir()
    (tmp_path / "fixture.yaml").write_text("owned parent fixture", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr("ifixai.core.fixture_loader._FIXTURES_DIR", fixtures)
    with pytest.raises(FileNotFoundError):
        resolve_fixture_path("..")
