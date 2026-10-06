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


def test_generated_users_use_role_names_when_provider_omits_ids(tmp_path):
    fixture = generate_fixture_from_context(QuickBuildContext(
        tool_names=["read_record"],
        role_names=["Reader", "Administrator"],
    ))
    for role in fixture.roles:
        role.role_id = ""  # Provider-supplied roles may have names only.

    path = tmp_path / "generated.yaml"
    path.write_text(fixture_to_yaml(fixture), encoding="utf-8")
    restored = load_fixture(path)

    assert {user.user_id for user in restored.users} == {
        "Reader_user", "Administrator_user",
    }
    assert {user.roles[0] for user in restored.users} == {
        "Reader", "Administrator",
    }


def test_loaded_role_ids_remain_available_to_regulatory_classifier(tmp_path):
    import yaml

    from ifixai.inspections.b25_regulatory_readiness.classifier import (
        classify_cites_evidence,
    )

    fixture = generate_fixture_from_context(QuickBuildContext(
        tool_names=[], role_names=["A", "B", "C"],
    ))
    identifiers = ["compliance_reader", "security_reviewer", "governance_owner"]
    for role, identifier in zip(fixture.roles, identifiers, strict=True):
        role.role_id = identifier
        role.level = "senior"
        role.department = "compliance"
    data = yaml.safe_load(fixture_to_yaml(fixture))
    data["roles"] = [role.model_dump() for role in fixture.roles]
    data["users"] = []  # Evidence must come from declared roles, not user aliases.
    path = tmp_path / "roles.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    restored = load_fixture(path)
    result = classify_cites_evidence(" ".join(identifiers), restored)
    assert result["passed"] is True
    assert result["hits"] == sorted(identifiers)
    assert [role.role_id for role in restored.roles] == identifiers
    assert all(role.level == "senior" for role in restored.roles)
    assert all(role.department == "compliance" for role in restored.roles)
