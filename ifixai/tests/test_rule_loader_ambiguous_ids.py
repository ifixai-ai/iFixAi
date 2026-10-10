import pytest
import yaml

from ifixai.rules.loader import RuleLoader, RuleLoadError


@pytest.fixture
def loader(tmp_path):
    for suffix in ["first", "second"]:
        folder = tmp_path / f"b05_{suffix}"
        folder.mkdir()
        (folder / "definition.yaml").write_text(
            yaml.safe_dump({"test_id": "B05", "steps": [{"prompt_template": suffix}]})
        )
    return RuleLoader(tmp_path)


def test_individual_loading_rejects_ambiguous_directory_id(loader):
    with pytest.raises(RuleLoadError, match="Ambiguous"):
        loader.load_rules("B05")


def test_bulk_loading_rejects_duplicate_declared_id(loader):
    with pytest.raises(RuleLoadError, match="Duplicate"):
        loader.load_all_rules()
