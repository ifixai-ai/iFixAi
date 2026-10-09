from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli


def test_cli_validate_builtin_fixture_name():
    runner = CliRunner()
    result = runner.invoke(ifixai_cli, ["validate", "default"])
    assert result.exit_code == 0, result.output
    assert "Valid" in result.output


def test_cli_validate_nonexistent_fixture_reports_error():
    runner = CliRunner()
    result = runner.invoke(ifixai_cli, ["validate", "nonexistent_fixture_name"])
    assert result.exit_code == 1, result.output
    assert "Validation failed with 1 error(s):" in result.output
    assert "Fixture not found: 'nonexistent_fixture_name'" in result.output
