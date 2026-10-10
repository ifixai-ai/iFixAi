from pathlib import Path

import pytest

from ifixai.cli.config_file import load_config


@pytest.mark.parametrize('document', ['[]', 'false', '0', '""'])
def test_falsey_nonmapping_config_is_rejected(tmp_path: Path, document: str) -> None:
    (tmp_path / 'ifixai.yaml').write_text(document, encoding='utf-8')
    with pytest.raises(ValueError, match='mapping'):
        load_config(tmp_path)


@pytest.mark.parametrize('document', ['', '# empty config', '{}', 'null'])
def test_empty_config_remains_supported(tmp_path: Path, document: str) -> None:
    (tmp_path / 'ifixai.yaml').write_text(document, encoding='utf-8')
    assert load_config(tmp_path) is not None
