from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ifixai.cli.validate import validate
from ifixai.core.fixture_loader import validate_fixture


@pytest.mark.parametrize("claim,expected", [
    ({"claim": "The record exists", "supported": True}, "source_id and evidence"),
    ({"claim": "The record does not exist", "supported": False}, "requires reason"),
])
def test_validation_rejects_claims_the_runtime_cannot_load(tmp_path, claim, expected):
    raw = yaml.safe_load((Path(__file__).parents[1] / "fixtures/examples/healthcare.yaml").read_text())
    raw["expected_claims"] = [claim]
    path = tmp_path / "claims.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    errors = validate_fixture(path)
    assert any(expected in error for error in errors)
    result = CliRunner().invoke(validate, [str(path)])
    assert result.exit_code == 1
    assert expected in result.output


def test_valid_fixture_still_validates():
    assert validate_fixture("healthcare") == []
