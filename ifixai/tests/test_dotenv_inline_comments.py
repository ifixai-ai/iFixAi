from pathlib import Path

import pytest

from ifixai.cli.init import load_dotenv_file


@pytest.mark.parametrize('assignment,expected', [('owned-value # explanatory comment', 'owned-value'), ('"owned-value" # explanatory comment', 'owned-value'), ("'owned-value' # explanatory comment", 'owned-value'), ('owned#value', 'owned#value'), ('"owned # value"', 'owned # value')])
def test_dotenv_comments_do_not_become_part_of_credential(tmp_path: Path, monkeypatch, assignment, expected):
    key = 'IFIXAI_OWNED_TEST_CREDENTIAL'
    monkeypatch.delenv(key, raising=False)
    path = tmp_path / '.env'
    path.write_text(key + '=' + assignment + '\n', encoding='utf-8')
    assert load_dotenv_file(path) == [key]
    import os
    assert os.environ[key] == expected
    monkeypatch.delenv(key)
