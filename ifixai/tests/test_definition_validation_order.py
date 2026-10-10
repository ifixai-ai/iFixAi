import pytest
import yaml

from ifixai.rules import loader as module


@pytest.mark.parametrize("steps", [[], ["not a step"], {"step_id": 1}])
def test_existing_invalid_definition_reports_schema_error(tmp_path, monkeypatch, steps):
    folder = tmp_path / "b05_custom"
    folder.mkdir()
    (folder / "definition.yaml").write_text(
        yaml.safe_dump({"test_id": "B05", "fixture_requirements": [], "steps": steps})
    )
    monkeypatch.setattr(module, "_DEFAULT_LOADER", module.RuleLoader(tmp_path))
    with pytest.raises(module.RuleLoadError, match="Schema validation failed"):
        module.load_inspection_definition("B05")


def test_missing_definition_remains_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_DEFAULT_LOADER", module.RuleLoader(tmp_path))
    assert module.load_inspection_definition("B05") is None
