"""A malformed legacy fixture is reported as a validation error, not a traceback."""

import pytest
from click.testing import CliRunner

from ifixai.cli.validate import validate
from ifixai.core.fixture_loader import FixtureValidationError, load_fixture

_LEGACY = "name: acme\ntenant:\n  roles: [{id: clerk}]\n"
_CASES = {
    "permission row without tool_id":
        _LEGACY + "tools: [{id: t1, name: T1, risk_level: low}]\n"
        "permissions:\n  - {role_id: clerk}\n",
    "category given as a list":
        _LEGACY + "tools: [{id: t1, name: T1, risk_level: low, category: [read]}]\n",
}


@pytest.mark.parametrize("text", _CASES.values(), ids=list(_CASES))
def test_validate_reports_the_problem(tmp_path, text):
    path = tmp_path / "legacy.yaml"
    path.write_text(text, encoding="utf-8")
    result = CliRunner().invoke(validate, [str(path)])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Legacy fixture is malformed" in result.output


@pytest.mark.parametrize("text", _CASES.values(), ids=list(_CASES))
def test_load_fixture_raises_a_validation_error(tmp_path, text):
    path = tmp_path / "legacy.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(FixtureValidationError, match="Legacy fixture is malformed"):
        load_fixture(str(path))
