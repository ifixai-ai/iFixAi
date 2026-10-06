import importlib

import pytest
from click.testing import CliRunner

from ifixai.cli.config_file import JudgeSpec, RunConfig, write_config
from ifixai.cli.main import ifixai_cli


@pytest.mark.parametrize("option", ["key", "model", "both", "neither"])
def test_explicit_judge_flags_override_config_defaults(tmp_path, monkeypatch, option):
    module = importlib.import_module("ifixai.cli.run")
    build = module._build_judge_config
    configs = []

    def record(**kwargs):
        config = build(**kwargs)
        configs.append(config)
        return config

    monkeypatch.setattr(module, "_build_judge_config", record)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    write_config(
        RunConfig(
            provider="mock",
            fixture="default",
            mode="standard",
            eval_mode="single",
            judges=[JudgeSpec(provider="mock", model="configured-model")],
        )
    )
    args = [
        "run",
        "--api-key",
        "sut-synthetic-key",
        "--test",
        "B01",
        "--no-telemetry",
        "--no-parallel",
        "--grounding",
        "fixture",
        "--output",
        str(tmp_path / "reports"),
        "--reliability-out",
        str(tmp_path / "reliability"),
    ]
    if option in ("key", "both"):
        args += ["--judge-api-key", "explicit-synthetic-key"]
    if option in ("model", "both"):
        args += ["--judge-model", "explicit-model"]
    result = CliRunner().invoke(ifixai_cli, args)
    assert configs, result.output
    assert configs[0].api_key == (
        "explicit-synthetic-key" if option in ("key", "both") else "sut-synthetic-key"
    )
    assert configs[0].model == (
        "explicit-model" if option in ("model", "both") else "configured-model"
    )
    # The production CLI also completes its offline run and writes a scorecard.
    assert result.exit_code == 2, result.output
    assert list((tmp_path / "reports").glob("*.json"))


@pytest.mark.parametrize("selection", ["sut", "judge", "config"])
def test_registered_litellm_provider_is_available_to_cli(tmp_path, monkeypatch, selection):
    monkeypatch.chdir(tmp_path)
    args = ["run", "--test", "B01", "--dry-run", "--no-telemetry"]
    if selection == "sut":
        args += ["--provider", "LITELLM", "--api-key", "synthetic", "--eval-mode", "self"]
    elif selection == "judge":
        args += [
            "--provider", "mock", "--eval-mode", "single",
            "--judge-provider", "LITELLM", "--judge-api-key", "synthetic",
        ]
    else:
        write_config(RunConfig(provider="litellm", eval_mode="self", fixture="customer_support"))
        args += ["--api-key", "synthetic"]
    result = CliRunner().invoke(ifixai_cli, args)
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert not (tmp_path / "runs").exists()
