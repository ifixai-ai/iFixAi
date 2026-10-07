"""Schema-valid YAML metadata dates must reach native runs and JSON exports."""

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ifixai.core.fixture_loader import load_fixture, validate_fixture
from ifixai.utils.fixture_digest import compute_fixture_digest


def _fixture(tmp_path, value):
    source = Path(__file__).parents[1] / "fixtures/examples/openclaw_strict.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    data["test_cases"][0]["metadata"] = {"reviewed_on": value}
    path = tmp_path / "fixture.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("value", [
    datetime.date(2026, 10, 6),
    datetime.datetime(2026, 10, 6, 12, 30, tzinfo=datetime.timezone.utc),
    "2026-10-06",
])
def test_valid_metadata_scalar_completes_native_cli(tmp_path, value):
    path = _fixture(tmp_path, value)
    assert validate_fixture(path) == []
    loaded = load_fixture(str(path))
    json_metadata = loaded.test_cases[0].model_dump(mode="json")["metadata"]
    assert isinstance(json_metadata["reviewed_on"], str)
    assert len(compute_fixture_digest(path)) == 64
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               IFIXAI_TELEMETRY="0", DO_NOT_TRACK="1")
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).parents[2]), env.get("PYTHONPATH", "")])
    result = subprocess.run([
        sys.executable, "-m", "ifixai.cli.main", "run", "--provider", "mock",
        "--fixture", str(path), "--test", "B01", "--eval-mode", "single",
        "--judge-provider", "mock", "--grounding", "fixture", "--no-telemetry",
        "--no-parallel", "--min-score", "0", "--output", "reports",
        "--reliability-out", "runs",
    ], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode in (0, 2), result.stderr
    assert "Traceback" not in result.stderr
    reports = list((tmp_path / "reports").glob("*.json"))
    assert len(reports) == 1, result.stdout
    scorecard = json.loads(reports[0].read_text(encoding="utf-8"))
    assert len(scorecard["test_results"]) == 1
    assert list((tmp_path / "runs").glob("*/manifest.json"))


@pytest.mark.parametrize("value", [
    datetime.date(2026, 10, 6),
    datetime.datetime(2026, 10, 6, 12, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=2))),
])
def test_dates_follow_existing_iso_json_scalar_convention(tmp_path, value):
    path = _fixture(tmp_path, value)
    digest = compute_fixture_digest(path)
    _fixture(tmp_path, value.isoformat())
    assert compute_fixture_digest(path) == digest


def test_ordinary_fixture_digest_bytes_are_unchanged(tmp_path):
    import hashlib

    path = tmp_path / "ordinary.yaml"
    path.write_text("text: café\ncount: 2\nlist: [false, null, 1.5]\n", encoding="utf-8")
    expected = json.dumps(yaml.safe_load(path.read_text()), sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert compute_fixture_digest(path) == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("key", [
    datetime.date(2026, 10, 6),
    datetime.datetime(2026, 10, 6, 12, 30, tzinfo=datetime.timezone.utc),
])
def test_date_keys_follow_iso_representation_with_mixed_metadata(tmp_path, key):
    path = _fixture(tmp_path, {key: "approved", "other": "unchanged"})
    assert validate_fixture(path) == []
    assert load_fixture(str(path)).test_cases[0].metadata
    digest = compute_fixture_digest(path)
    _fixture(tmp_path, {key: "approved"})
    assert len(compute_fixture_digest(path)) == 64
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), IFIXAI_TELEMETRY="0", DO_NOT_TRACK="1")
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).parents[2]), env.get("PYTHONPATH", "")])
    result = subprocess.run([
        sys.executable, "-m", "ifixai.cli.main", "run", "--provider", "mock",
        "--fixture", str(path), "--test", "B01", "--eval-mode", "single",
        "--judge-provider", "mock", "--grounding", "fixture", "--no-telemetry",
        "--no-parallel", "--min-score", "0", "--output", "reports",
        "--reliability-out", "runs",
    ], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode in (0, 2), result.stderr
    assert "Traceback" not in result.stderr
    assert len(list((tmp_path / "reports").glob("*.json"))) == 1
    assert len(list((tmp_path / "runs").glob("*/manifest.json"))) == 1
    _fixture(tmp_path, {key.isoformat(): "approved", "other": "unchanged"})
    assert compute_fixture_digest(path) == digest


def test_date_key_conversion_cannot_silently_merge_distinct_entries(tmp_path):
    path = _fixture(tmp_path, {
        datetime.date(2026, 10, 6): "approved",
        "2026-10-06": "different original entry",
    })
    assert validate_fixture(path) == []
    with pytest.raises(ValueError, match="duplicate canonical fixture key"):
        compute_fixture_digest(path)
