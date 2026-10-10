import pytest

from ifixai.cli.config_file import load_config


def test_invalid_utf8_config_uses_the_public_load_error_contract(tmp_path):
    (tmp_path / "ifixai.yaml").write_bytes(b"provider: \xff\n")
    with pytest.raises(ValueError, match=r"Could not read ifixai\.yaml"):
        load_config(tmp_path)


def test_cli_identifies_the_invalidly_encoded_config(tmp_path):
    from ifixai.tests.test_cli_config_read_errors import _run

    (tmp_path / "ifixai.yaml").write_bytes(b"provider: \xff\n")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "Config error: Could not read ifixai.yaml:" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Dry run" not in result.stdout
