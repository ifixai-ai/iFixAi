from pathlib import Path

import pytest
import yaml

from ifixai.core.fixture_loader import load_fixture, validate_fixture
from ifixai.utils.fixture_digest import compute_fixture_digest


def write_fixture(tmp_path, counts):
    source = Path(__file__).parents[1] / "fixtures/examples/openclaw_strict.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["test_cases"][0]["metadata"] = {"counts": counts}
    path = tmp_path / "fixture.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_nested_numeric_metadata_keys_follow_json_key_convention(tmp_path):
    path = write_fixture(tmp_path, {1: "one", "other": "two"})
    assert validate_fixture(path) == []
    assert load_fixture(path).test_cases[0].metadata
    digest = compute_fixture_digest(path)
    write_fixture(tmp_path, {"1": "one", "other": "two"})
    assert compute_fixture_digest(path) == digest


def test_json_key_collisions_are_rejected(tmp_path):
    path = write_fixture(tmp_path, {1: "one", "1": "different"})
    with pytest.raises(ValueError, match="duplicate canonical fixture key"):
        compute_fixture_digest(path)
