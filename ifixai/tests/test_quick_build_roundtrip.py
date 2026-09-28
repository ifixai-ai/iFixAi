"""The Quick Build YAML must retain the coverage created in memory."""

from ifixai.core.fixture_loader import load_fixture, validate_fixture
from ifixai.core.types import Regulation
from ifixai.quick_build import (
    QuickBuildContext,
    fixture_to_yaml,
    generate_fixture_from_context,
)


def test_quick_build_yaml_preserves_cases_and_regulations(tmp_path):
    fixture = generate_fixture_from_context(QuickBuildContext(
        tool_names=["read_record", "delete_record"],
        role_names=["user", "admin"],
    ))
    fixture.regulations.append(Regulation(name="Local policy", required_tests=["B01"]))
    fixture.policies.override_allowed_roles = ["admin"]
    fixture.policies.safety_rules = ["Never export secret records"]
    fixture.data_sources[0].accessible_by_roles = ["admin"]
    fixture.metadata.on_topic_examples = ["Find my invoice"]
    fixture.tools[0].tool_behavior = "read only"
    expected_cases = len(fixture.test_cases)
    assert expected_cases > 0

    path = tmp_path / "generated.yaml"
    path.write_text(fixture_to_yaml(fixture), encoding="utf-8")

    assert validate_fixture(path) == []
    restored = load_fixture(path)
    assert len(restored.test_cases) == expected_cases
    assert {(case.test, case.expected_result) for case in restored.test_cases} == {
        ("SSCI-B01", "allow"), ("SSCI-B08", "deny"),
    }
    assert restored.regulations == fixture.regulations
    assert restored.policies.override_allowed_roles == ["admin"]
    assert restored.policies.safety_rules == ["Never export secret records"]
    assert restored.data_sources[0].accessible_by_roles == ["admin"]
    assert restored.metadata.on_topic_examples == ["Find my invoice"]
    assert restored.tools[0].tool_behavior == "read only"
