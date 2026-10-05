"""Distinct CLI run manifests must retain distinct exported reports."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli


@pytest.mark.parametrize("change", ["model", "nonce-suffix", "resume"])
def test_native_cli_keeps_reports_for_distinct_run_ids(tmp_path, monkeypatch, change):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    fixture = Path(__file__).parents[1] / "fixtures/examples/customer_support.yaml"
    common = ["run", "--provider", "mock", "--api-key", "owned-unused",
              "--name", "Owned deployment", "--fixture", str(fixture),
              "--test", "B01", "--eval-mode", "self", "--format", "json",
              "--output", str(tmp_path / "reports"), "--no-telemetry",
              "--no-parallel", "--reliability-out", str(tmp_path / "runs")]
    original = CliRunner().invoke(ifixai_cli, [*common, "--model", "owned-first",
                                              "--run-nonce", "0123456789abcdef"])
    assert original.exit_code == 2, original.output
    assert "Total execution time:" in original.output
    first_files = list((tmp_path / "reports").glob("*.json"))
    assert len(first_files) == 1
    original_bytes = first_files[0].read_bytes()
    model = "owned-second" if change == "model" else "owned-first"
    nonce = "0123456789fedcba" if change == "nonce-suffix" else "0123456789abcdef"
    resume = []
    if change == "resume":
        run_id = next((tmp_path / "runs").glob("*/manifest.json")).parent.name
        resume = ["--resume", run_id]
    later = CliRunner().invoke(ifixai_cli, [*common, "--model", model, "--run-nonce", nonce, *resume])
    assert later.exit_code == 2, later.output
    expected = 1 if change == "resume" else 2
    assert len(list((tmp_path / "runs").glob("*/manifest.json"))) == expected
    assert len(list((tmp_path / "reports").glob("*.json"))) == expected
    if change != "resume":
        assert first_files[0].read_bytes() == original_bytes
