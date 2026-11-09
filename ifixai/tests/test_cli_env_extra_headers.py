"""A malformed IFIXAI_EXTRA_HEADERS fails `ifixai run` up front, like --extra-headers."""

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli

_MALFORMED = '{"X-Tenant": "acme", "X-Agent-Key": "owned",}'


def _dry_run(tmp_path, monkeypatch, provider, env_value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    if env_value is None:
        monkeypatch.delenv("IFIXAI_EXTRA_HEADERS", raising=False)
    else:
        monkeypatch.setenv("IFIXAI_EXTRA_HEADERS", env_value)
    args = ["run", "--provider", provider, "--test", "B01", "--eval-mode", "self",
            "--dry-run", "--no-telemetry", "--no-promo"]
    if provider == "http":
        args += ["--endpoint", "http://127.0.0.1:9/v1", "--auth-method", "none"]
    return CliRunner().invoke(ifixai_cli, args)


@pytest.mark.parametrize("value", [_MALFORMED, '["X-Tenant"]'])
def test_malformed_env_headers_fail_like_the_flag(tmp_path, monkeypatch, value):
    result = _dry_run(tmp_path, monkeypatch, "http", value)
    assert result.exit_code == 2, result.output
    assert "IFIXAI_EXTRA_HEADERS must be a JSON object" in result.output


def test_valid_env_headers_are_accepted(tmp_path, monkeypatch):
    result = _dry_run(tmp_path, monkeypatch, "http", '{"X-Tenant": "acme"}')
    assert result.exit_code == 0, result.output


def test_providers_that_ignore_the_env_var_are_unaffected(tmp_path, monkeypatch):
    result = _dry_run(tmp_path, monkeypatch, "mock", _MALFORMED)
    assert result.exit_code == 0, result.output
