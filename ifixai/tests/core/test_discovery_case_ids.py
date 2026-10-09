"""Generated case IDs must stay attached to the same tool across runs."""

from ifixai.core.discovery import generate_test_cases
from ifixai.core.types import Permission, Role, Tool


def test_generated_case_ids_follow_stable_tool_order():
    roles = [Role(name="member", role_id="member")]
    tools = [
        Tool(tool_id=tool_id, name=tool_id)
        for tool_id in (
            "tool_zeta", "tool_beta", "tool_epsilon", "tool_alpha",
            "tool_delta", "tool_gamma", "tool_eta", "tool_theta",
        )
    ]
    permissions = [Permission(
        role="member",
        tools=["tool_zeta", "tool_gamma", "tool_alpha", "tool_delta"],
    )]

    cases = generate_test_cases(roles, permissions, tools)

    assert [(case.test_id, case.test, case.tool_id) for case in cases] == [
        ("tc-001", "B01", "tool_alpha"),
        ("tc-002", "B01", "tool_delta"),
        ("tc-003", "B01", "tool_gamma"),
        ("tc-004", "B01", "tool_zeta"),
        ("tc-005", "B08", "tool_beta"),
        ("tc-006", "B08", "tool_epsilon"),
        ("tc-007", "B08", "tool_eta"),
        ("tc-008", "B08", "tool_theta"),
    ]


def test_schema_fixture_cases_match_regulation_required_test_ids(tmp_path):
    import yaml

    from ifixai.core.fixture_loader import load_fixture, validate_fixture
    from ifixai.core.types import Regulation
    from ifixai.quick_build import (
        QuickBuildContext,
        fixture_to_yaml,
        generate_fixture_from_context,
    )

    fixture = generate_fixture_from_context(QuickBuildContext(
        tool_names=["read_record"], role_names=["reader"],
    ))
    fixture.regulations = [Regulation(name="Local policy", required_tests=["B01", "B08"])]
    path = tmp_path / "coverage.yaml"
    path.write_text(fixture_to_yaml(fixture), encoding="utf-8")
    assert validate_fixture(path) == []
    restored = load_fixture(path)
    assert restored.has_coverage_for("B01")
    assert restored.has_coverage_for("SSCI-B01")
    assert restored.test_cases_for_test("B01") == restored.test_cases
    assert not restored.has_coverage_for("B08")
    assert restored.test_cases_for_test("B08") == []
    # In-memory generated cases use bare IDs; both sides must accept the prefix.
    assert fixture.has_coverage_for("SSCI-B01")
    assert fixture.test_cases_for_test("SSCI-B01") == fixture.test_cases
    data = yaml.safe_load(path.read_text())
    assert data["test_cases"][0]["test"] == "SSCI-B01"
