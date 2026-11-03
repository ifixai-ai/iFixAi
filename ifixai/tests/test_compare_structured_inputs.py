"""Comparison rejects ambiguous or malformed evidence instead of crashing or shadowing."""

import json

import pytest
from click.testing import CliRunner

from ifixai.cli.compare import compare


@pytest.mark.parametrize("payload", [
    [], None, "scorecard", {"metadata": []}, {"overall": None},
    {"test_results": {}}, {"category_scores": "bad"},
    {"test_results": [None]}, {"test_results": [{}]},
    {"test_results": [{"test_id": "B19", "status": "unknown"}]},
    {"test_results": [{"test_id": "B19", "score": "not a number"}]},
    {"test_results": [{"test_id": "B19", "score": 0.0},
                      {"test_id": "B19", "score": 1.0}]},
])
@pytest.mark.parametrize("side", [0, 1])
def test_invalid_comparison_shape_has_actionable_error(tmp_path, payload, side):
    valid, invalid = tmp_path / "valid.json", tmp_path / "invalid.json"
    valid.write_text("{}", encoding="utf-8")
    invalid.write_text(json.dumps(payload), encoding="utf-8")
    paths = [str(valid), str(valid)]
    paths[side] = str(invalid)
    result = CliRunner().invoke(compare, paths)
    assert result.exit_code == 1
    assert "Error: Invalid scorecard" in result.output
    assert repr(str(invalid)) in result.output
    assert "Traceback" not in result.output


def test_legacy_minimal_object_still_loads(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text("{}", encoding="utf-8")
    assert CliRunner().invoke(compare, [str(path), str(path)]).exit_code == 0
