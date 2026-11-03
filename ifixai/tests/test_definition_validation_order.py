import pytest
import yaml

from ifixai.rules import loader as module


def test_definition_schema_validator_is_reused_and_invalidated(tmp_path):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text('{"type":"object"}', encoding="utf-8")
    first_stat = schema_path.stat()

    first = module._definition_validator(
        schema_path, first_stat.st_mtime_ns, first_stat.st_size
    )
    second = module._definition_validator(
        schema_path, first_stat.st_mtime_ns, first_stat.st_size
    )
    assert first is second

    schema_path.write_text('{"type":"array"}', encoding="utf-8")
    changed_stat = schema_path.stat()
    changed = module._definition_validator(
        schema_path, changed_stat.st_mtime_ns, changed_stat.st_size
    )
    assert changed is not first


@pytest.mark.parametrize("steps", [["not a step"], {"step_id": 1}])
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


async def test_invalid_b26_definition_is_configuration_error_without_sut_calls(tmp_path, monkeypatch):
    from ifixai.core.fixture_loader import load_fixture
    from ifixai.core.runner import run_single
    from ifixai.core.types import ProviderConfig, TestStatus
    from ifixai.providers.base import ChatProvider

    folder = tmp_path / "b26_rate_limiting"
    folder.mkdir()
    (folder / "definition.yaml").write_text(yaml.safe_dump({"test_id": "B26", "steps": ["not a step"]}))
    monkeypatch.setattr(module, "_DEFAULT_LOADER", module.RuleLoader(tmp_path))
    class OwnedProvider(ChatProvider):
        calls = 0
        async def send_message(self, messages, config):
            self.calls += 1
            return "owned reply"
    provider = OwnedProvider()
    result = await run_single("B26", provider, ProviderConfig(provider="owned"), load_fixture("customer_support"))
    assert result.status == TestStatus.ERROR
    assert "Schema validation failed" in result.error
    assert provider.calls == 0


@pytest.mark.parametrize("test_id", ["B28", "B29", "B31", "P01", "P08"])
def test_shipped_custom_runner_definition_remains_optional(monkeypatch, test_id):
    monkeypatch.setattr(module, "_DEFAULT_LOADER", module.RuleLoader())
    assert module.load_inspection_definition(test_id) is None


@pytest.mark.parametrize("raw", [{"test_id": "B05"}, {"test_id": "B05", "steps": []}])
def test_definition_without_declarative_steps_remains_optional(tmp_path, monkeypatch, raw):
    folder = tmp_path / "b05_custom"
    folder.mkdir()
    (folder / "definition.yaml").write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(module, "_DEFAULT_LOADER", module.RuleLoader(tmp_path))
    assert module.load_inspection_definition("B05") is None
