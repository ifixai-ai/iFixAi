"""Persisted configuration read failures use the CLI's existing diagnostic."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ifixai.cli.config_file import load_config


def _run(directory):
    env = os.environ.copy()
    env.update(
        HOME=str(directory),
        XDG_CONFIG_HOME=str(directory / "owned-config"),
        IFIXAI_TELEMETRY="0",
        DO_NOT_TRACK="1",
    )
    package_root = str(Path(__file__).parents[2])
    env["PYTHONPATH"] = os.pathsep.join(
        [package_root, env.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [
            sys.executable, "-m", "ifixai.cli.main", "run",
            "--provider", "mock", "--fixture", "customer_support",
            "--test", "B01", "--eval-mode", "single",
            "--judge-provider", "mock", "--dry-run", "--no-telemetry",
        ],
        cwd=directory,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_config_directory_reports_read_error(tmp_path):
    (tmp_path / "ifixai.yaml").mkdir()
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "Config error: Could not read ifixai.yaml:" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Dry run" not in result.stdout


def test_unreadable_config_reports_read_error(tmp_path):
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("Requires native POSIX permission enforcement for this user")
    path = tmp_path / "ifixai.yaml"
    path.write_text("provider: mock\nfixture: customer_support\n", encoding="utf-8")
    path.chmod(0)
    try:
        result = _run(tmp_path)
        assert result.returncode == 1
        assert "Config error: Could not read ifixai.yaml:" in result.stderr
        assert "Permission denied" in result.stderr
        assert "Traceback" not in result.stderr
        assert "Dry run" not in result.stdout
    finally:
        path.chmod(0o600)


@pytest.mark.parametrize("config", [None, "provider: mock\nfixture: customer_support\n"])
def test_readable_or_missing_config_still_allows_dry_run(tmp_path, config):
    if config is not None:
        (tmp_path / "ifixai.yaml").write_text(config, encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Dry run -- no API calls will be made" in result.stdout
    assert "Traceback" not in result.stderr


def test_yaml_validation_keeps_its_existing_error(tmp_path):
    (tmp_path / "ifixai.yaml").write_text("provider: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="is not valid YAML"):
        load_config(tmp_path)
