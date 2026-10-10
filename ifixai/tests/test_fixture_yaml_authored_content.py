from ifixai.core.fixture_loader import load_fixture, validate_fixture
from ifixai.core.types import ExpectedClaim, User
from ifixai.quick_build import (
    QuickBuildContext,
    fixture_to_yaml,
    generate_fixture_from_context,
)


def test_fixture_export_preserves_authored_users_and_diagnostic_context(tmp_path):
    fixture = generate_fixture_from_context(
        QuickBuildContext(tool_names=["read"], role_names=["analyst"])
    )
    fixture.users = [User(user_id="pat", name="Pat", roles=["analyst"])]
    fixture.roles[0].department = "Research"
    fixture.system_purpose = "Find research records"
    fixture.expected_escalation_channels = ["Research lead"]
    fixture.expected_claims = [
        ExpectedClaim(
            claim="The owner is Pat.",
            supported=True,
            source_id="knowledge_base",
            evidence="The owner is Pat.",
        )
    ]
    path = tmp_path / "fixture.yaml"
    path.write_text(fixture_to_yaml(fixture), encoding="utf-8")
    assert validate_fixture(path) == []
    restored = load_fixture(path)
    assert restored.users == fixture.users
    assert restored.roles == fixture.roles
    assert restored.system_purpose == fixture.system_purpose
    assert restored.expected_escalation_channels == fixture.expected_escalation_channels
    assert restored.expected_claims == fixture.expected_claims
