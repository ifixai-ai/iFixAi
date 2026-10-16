import pytest
from click.testing import CliRunner

from ifixai.cli.compare import compare


@pytest.mark.parametrize("argument", [0, 1])
@pytest.mark.parametrize("content", [b'{"metadata":', b'\xff'])
def test_compare_reports_invalid_input(tmp_path, argument, content):
    valid = tmp_path / "valid.json"
    valid.write_text("{}", encoding="utf-8")
    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(content)
    arguments = [str(valid), str(valid)]
    arguments[argument] = str(invalid)

    result = CliRunner().invoke(compare, arguments)

    assert result.exit_code == 1
    assert "Error: Could not read scorecard" in result.output
    assert repr(str(invalid)) in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("argument", [0, 1])
def test_compare_rejects_directories(tmp_path, argument):
    valid = tmp_path / "valid.json"
    valid.write_text("{}", encoding="utf-8")
    arguments = [str(valid), str(valid)]
    arguments[argument] = str(tmp_path)

    result = CliRunner().invoke(compare, arguments)

    assert result.exit_code == 2
    assert "is a directory" in result.output


def test_compare_reports_read_failure(tmp_path, monkeypatch):
    valid = tmp_path / "valid.json"
    valid.write_text("{}", encoding="utf-8")

    def deny_read(*args, **kwargs):
        raise PermissionError("Permission denied")

    monkeypatch.setattr("builtins.open", deny_read)
    result = CliRunner().invoke(compare, [str(valid), str(valid)])

    assert result.exit_code == 1
    assert "Error: Could not read scorecard" in result.output
    assert "Permission denied" in result.output
