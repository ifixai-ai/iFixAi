from pathlib import Path

import pytest
from click.testing import CliRunner

from ifixai.cli.validate import validate
from ifixai.core.fixture_loader import validate_fixture


@pytest.mark.parametrize(
    ("filename", "contents", "expected"),
    [
        ("broken.yaml", "metadata: [unclosed", "YAML"),
        ("broken.json", '{"metadata":', "JSON"),
        ("list.yaml", "- one\n- two\n", "Fixture must be a mapping"),
    ],
)
def test_invalid_fixture_is_reported_without_traceback(
    tmp_path: Path, filename: str, contents: str, expected: str
) -> None:
    path = tmp_path / filename
    path.write_text(contents)

    errors = validate_fixture(path)
    assert len(errors) == 1
    assert expected.lower() in errors[0].lower()

    result = CliRunner().invoke(validate, [str(path)])
    assert result.exit_code == 1
    assert "Validation failed with 1 error(s):" in result.output
    assert expected.lower() in result.output.lower()
    assert "Traceback" not in result.output


def test_directory_is_reported_as_invalid_fixture(tmp_path: Path) -> None:
    errors = validate_fixture(tmp_path)
    assert len(errors) == 1

    result = CliRunner().invoke(validate, [str(tmp_path)])
    assert result.exit_code == 1
    assert "Validation failed with 1 error(s):" in result.output
    assert "Traceback" not in result.output
