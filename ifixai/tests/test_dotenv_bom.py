"""UTF-8 BOMs written by editors must not become part of environment keys."""

import os

import pytest

from ifixai.cli.init import load_dotenv_file


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
@pytest.mark.parametrize("prefix", ["", "export "])
def test_dotenv_first_assignment_accepts_optional_bom(tmp_path, monkeypatch, encoding, prefix):
    key = "IFIXAI_TEST_DOTENV_BOM"
    monkeypatch.setattr(os, "environ", {})
    path = tmp_path / ".env"
    path.write_text(f'{prefix}{key}="fixture-value" # comment\n', encoding=encoding)

    assert load_dotenv_file(path) == [key]
    assert os.environ[key] == "fixture-value"
    assert "\ufeff" + key not in os.environ


def test_dotenv_bom_preserves_existing_environment(tmp_path, monkeypatch):
    key = "IFIXAI_TEST_DOTENV_BOM"
    monkeypatch.setattr(os, "environ", {key: "shell-value"})
    path = tmp_path / ".env"
    path.write_text(f"{key}=file-value\n", encoding="utf-8-sig")

    assert load_dotenv_file(path) == []
    assert os.environ[key] == "shell-value"
    assert "\ufeff" + key not in os.environ
