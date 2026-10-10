"""Saved CLI choices must obey the same contract as explicit flags."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli


def _invoke(tmp_path, monkeypatch, config, extra=()):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    (tmp_path / "ifixai.yaml").write_text(config, encoding="utf-8")
    fixture = Path(__file__).parents[1] / "fixtures/examples/customer_support.yaml"
    return CliRunner().invoke(
        ifixai_cli,
        [
            "run",
            "--provider",
            "mock",
            "--api-key",
            "owned-unused-key",
            "--fixture",
            str(fixture),
            "--test",
            "B01",
            "--eval-mode",
            "self",
            "--output",
            str(tmp_path / "reports"),
            "--reliability-out",
            str(tmp_path / "runs"),
            "--min-score",
            "0",
            "--no-telemetry",
            "--no-parallel",
            *extra,
        ],
    )


def test_config_format_is_normalized_and_report_files_are_written(
    tmp_path, monkeypatch
):
    result = _invoke(tmp_path, monkeypatch, "format: BOTH\n")
    assert result.exit_code == 2, result.output
    assert "Total execution time:" in result.output
    assert len(list((tmp_path / "reports").glob("*.json"))) == 1
    assert len(list((tmp_path / "reports").glob("*.md"))) == 2


@pytest.mark.parametrize(
    "config,option",
    [
        ("format: csv\n", "--format"),
        ("mode: typo\n", "--mode"),
        ("auth_method: typo\n", "--auth-method"),
        ("grounding: typo\n", "--grounding"),
        ("judges: [{provider: typo}]\n", "--judge-provider"),
    ],
)
def test_invalid_saved_choice_fails_before_a_run(tmp_path, monkeypatch, config, option):
    result = _invoke(tmp_path, monkeypatch, config)
    assert result.exit_code == 2, result.output
    assert option in result.output
    assert "Invalid value" in result.output
    assert not (tmp_path / "runs").exists()


def test_explicit_flag_overrides_invalid_saved_choice(tmp_path, monkeypatch):
    result = _invoke(tmp_path, monkeypatch, "format: csv\n", ["--format", "json"])
    assert result.exit_code == 2, result.output
    assert "Total execution time:" in result.output
    assert len(list((tmp_path / "reports").glob("*.json"))) == 1


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("provider", "MOCK", "mock"),
        ("auth_method", "NONE", "none"),
        ("grounding", "FIXTURE", "fixture"),
        ("run_mode", "FULL", "full"),
        ("eval_mode", "SELF", "self"),
        ("report_format", "BOTH", "both"),
        ("judge_provider", ("MOCK",), ("mock",)),
    ],
)
def test_saved_choices_use_cli_case_insensitive_contract(name, value, expected):
    import click
    from click.core import ParameterSource

    from ifixai.cli.run import _cfg_value, run

    ctx = click.Context(run)
    ctx.set_parameter_source(name, ParameterSource.DEFAULT)
    assert _cfg_value(ctx, name, None, value) == expected


@pytest.mark.parametrize("mode,eval_mode,panel_size", [
    ("full", "full", 2), ("full", "full", 4),
    ("standard", "full", 3), ("full", "deterministic", 0),
    ("standard", "single", 1),
])
def test_dry_run_estimates_actual_evaluation_panel(tmp_path, monkeypatch, mode, eval_mode, panel_size):
    monkeypatch.chdir(tmp_path)
    args = [
        "run", "--provider", "mock", "--fixture", "customer_support",
        "--test", "B01", "--mode", mode, "--eval-mode", eval_mode,
        "--dry-run", "--no-telemetry",
    ]
    for index in range(panel_size):
        args += ["--judge-provider", "mock" if index % 2 == 0 else "http"]
    result = CliRunner().invoke(ifixai_cli, args)
    assert result.exit_code == 0, result.output
    assert f"Judge calls per inspection: {panel_size}" in result.output
    assert f"Estimated judge calls: {10 * panel_size}" in result.output
    assert not (tmp_path / "runs").exists()
