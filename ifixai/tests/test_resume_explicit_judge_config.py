"""Resume cannot reuse verdicts from a different explicitly configured grader."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli
from ifixai.evaluation.manifest import load_manifest, verify_run_id


@pytest.mark.parametrize("change", ["model", "budget", "unchanged"])
def test_native_cli_resume_binds_explicit_judge_configuration(tmp_path, monkeypatch, change):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    from ifixai.providers.mock_governance import MockGovernanceProvider

    observed_models = []
    original_send = MockGovernanceProvider.send_message

    async def tracked_send(provider, messages, config):
        if config.model and config.model.startswith("owned-judge-"):
            observed_models.append(config.model)
        return await original_send(provider, messages, config)

    monkeypatch.setattr(MockGovernanceProvider, "send_message", tracked_send)
    fixture = Path(__file__).parents[1] / "fixtures/examples/customer_support.yaml"
    common = ["run", "--provider", "mock", "--model", "owned-sut", "--fixture", str(fixture),
              "--test", "B07", "--eval-mode", "single", "--judge-provider", "mock",
              "--output", str(tmp_path / "reports"), "--reliability-out", str(tmp_path / "runs"),
              "--no-telemetry", "--no-parallel"]
    first = CliRunner().invoke(ifixai_cli, [*common, "--judge-model", "owned-judge-a", "--judge-budget", "10000"])
    assert first.exit_code == 2, first.output
    assert observed_models and set(observed_models) == {"owned-judge-a"}
    paths = list((tmp_path / "runs").glob("*/manifest.json"))
    assert len(paths) == 1
    stored = json.loads(paths[0].read_text())
    assert verify_run_id(load_manifest(paths[0]))
    model = "owned-judge-b" if change == "model" else "owned-judge-a"
    budget = "1" if change == "budget" else "10000"
    resumed = CliRunner().invoke(ifixai_cli, [*common, "--judge-model", model,
        "--judge-budget", budget, "--resume", stored["run_id"]])
    if change == "unchanged":
        assert resumed.exit_code == 2, resumed.output
        assert "[reused] B07" in resumed.output
    else:
        assert resumed.exit_code == 1, resumed.output
        assert "run configuration changed" in resumed.output
        assert "[reused] B07" not in resumed.output


@pytest.mark.parametrize("version", [1, 3, 4])
def test_frozen_pre_grading_context_manifests_keep_original_identity(version):
    path = Path(__file__).parent / "fixtures" / f"manifest_before_judge_config_v{version}.json"
    original = json.loads(path.read_text())
    loaded = load_manifest(path)
    assert loaded.run_id == original["run_id"]
    assert loaded.evaluation_mode is None
    assert loaded.judge_budget == 0
    assert verify_run_id(loaded)


@pytest.mark.parametrize("mode", ["deterministic", "single", "semantic", "self", "full"])
def test_current_manifest_binds_each_supported_evaluation_mode(mode):
    from ifixai.core.types import RunMode
    from ifixai.evaluation.manifest import build_manifest
    from ifixai.evaluation.types import ModelDescriptor

    common = dict(mode=RunMode.STANDARD,
                  model_under_test=ModelDescriptor(provider="mock", model_id="owned-sut", version="1"),
                  judge_models=[], normalizer_version="1", test_versions={}, rubric_hashes={}, fixture_digest="a" * 64,
                  run_nonce="0123456789abcdef")
    configured = build_manifest(**common, evaluation_mode=mode, judge_budget=100)
    assert verify_run_id(configured)
    assert configured.evaluation_mode == mode
    assert configured.run_id != build_manifest(**common, evaluation_mode=None, judge_budget=100).run_id
    assert configured.run_id != build_manifest(**common, evaluation_mode=mode, judge_budget=1).run_id


def test_native_full_judge_roster_is_persisted_in_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    fixture = Path(__file__).parents[1] / "fixtures/examples/customer_support.yaml"
    run = CliRunner().invoke(ifixai_cli, ["run", "--provider", "mock", "--model", "owned-sut",
        "--fixture", str(fixture), "--test", "B07", "--eval-mode", "full",
        "--judge-provider", "mock", "--judge-model", "owned-judge-a",
        "--judge-provider", "mock", "--judge-model", "owned-judge-b",
        "--judge-budget", "10000", "--output", str(tmp_path / "reports"),
        "--reliability-out", str(tmp_path / "runs"), "--no-telemetry", "--no-parallel"])
    assert run.exit_code == 2, run.output
    manifest = load_manifest(next((tmp_path / "runs").glob("*/manifest.json")))
    assert [(m.provider, m.model_id) for m in manifest.judge_models] == [
        ("mock", "owned-judge-a"), ("mock", "owned-judge-b")]
    assert manifest.evaluation_mode == "full"
    assert manifest.judge_budget == 10000
    assert verify_run_id(manifest)
