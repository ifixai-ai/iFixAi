import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli


@pytest.mark.parametrize("saved", [False, True])
def test_full_mode_rejects_one_provider_repeated(tmp_path, monkeypatch, saved):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    args = ["run", "--provider", "mock", "--fixture", "customer_support",
            "--eval-mode", "full", "--test", "B01", "--dry-run", "--no-telemetry"]
    if saved:
        (tmp_path / "ifixai.yaml").write_text(
            "judges: [{provider: mock}, {provider: mock}]\n", encoding="utf-8")
    else:
        args += ["--judge-provider", "mock", "--judge-provider", "MOCK"]
    result = CliRunner().invoke(ifixai_cli, args)
    assert result.exit_code == 1
    assert "requires >=2 distinct --judge-provider" in result.output
    assert "Estimated judge calls" not in result.output


@pytest.mark.parametrize("providers", [("mock", "http"), ("mock", "http", "mock")])
def test_full_mode_accepts_at_least_two_distinct_providers(tmp_path, monkeypatch, providers):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    args = ["run", "--provider", "mock", "--fixture", "customer_support",
            "--eval-mode", "full", "--test", "B01", "--dry-run", "--no-telemetry"]
    for provider in providers:
        args += ["--judge-provider", provider]
    result = CliRunner().invoke(ifixai_cli, args)
    assert result.exit_code == 0, result.output
    assert "Estimated judge calls" in result.output


@pytest.mark.parametrize("providers", [(), ("mock", "http")])
def test_single_mode_requires_exactly_one_judge(tmp_path, monkeypatch, providers):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    args = ["run", "--provider", "mock", "--fixture", "customer_support",
            "--eval-mode", "single", "--test", "B01", "--dry-run", "--no-telemetry"]
    for provider in providers:
        args += ["--judge-provider", provider]
    result = CliRunner().invoke(ifixai_cli, args)
    assert result.exit_code == 1
    assert "requires exactly one --judge-provider" in result.output
    assert "Estimated judge calls" not in result.output


def test_single_mode_accepts_one_judge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    result = CliRunner().invoke(ifixai_cli, [
        "run", "--provider", "mock", "--fixture", "customer_support",
        "--eval-mode", "single", "--judge-provider", "mock", "--test", "B01",
        "--dry-run", "--no-telemetry"])
    assert result.exit_code == 0, result.output
