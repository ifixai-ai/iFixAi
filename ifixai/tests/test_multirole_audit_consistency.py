"""Cross-hook audit checks use the same role union as tool governance."""

import pytest

from ifixai import api
from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import ProviderConfig
from ifixai.harness.consistency import CrossHookValidator
from ifixai.inspections.b01_tool_governance.runner import build_permission_map
from ifixai.providers.mock_governance import MockGovernanceProvider


def owned_fixture(actor_kind="user", decision="allow", roles=None):
    fixture = load_fixture("default").model_copy(deep=True)
    user = fixture.users[0].model_copy(update={"roles": roles or ["external_vendor", "platform_admin"]})
    fixture.users = [user]
    governance = fixture.governance
    governance.policies = governance.policies.model_copy(update={
        "governance_architecture": governance.policies.governance_architecture.model_copy(
            update={"deterministic_control": True}
        )
    })
    governance.seed_audit_records = ()
    governance.audit_log.records = [{
        "timestamp": "2026-01-01T00:00:00Z",
        "actor": user.user_id if actor_kind == "user" else user.name if actor_kind == "name" else "platform_admin",
        "action": "invoke", "decision": decision,
        "tool_id": "status_dashboard_read", "policy_rule": "rule_for:platform_admin",
    }]
    return fixture


@pytest.mark.parametrize("actor_kind", ["user", "name"])
async def test_second_role_allow_does_not_cap_native_api_scorecard(actor_kind):
    fixture = owned_fixture(actor_kind)
    assert "status_dashboard_read" in build_permission_map(fixture)[fixture.users[0].user_id]
    result = await api.run_selected({"B02"}, provider="mock", fixture=fixture)
    assert not result.validation_warnings
    assert not result.score_capped
    assert result.test_results[0].score == 1.0


async def test_second_role_allow_makes_denial_a_real_contradiction():
    fixture = owned_fixture(decision="deny")
    provider = MockGovernanceProvider(governance=fixture.governance)
    violations = await CrossHookValidator().run(provider, ProviderConfig(provider="mock"), fixture)
    assert [v.check for v in violations] == ["authorize_contradicts_audit"]


@pytest.mark.parametrize("actor_kind,decision,roles,expected", [
    ("role", "allow", None, []),
    ("user", "deny", ["external_vendor"], []),
    ("user", "allow", ["external_vendor"], ["authorize_contradicts_audit"]),
])
async def test_role_actor_and_genuinely_denied_controls(actor_kind, decision, roles, expected):
    fixture = owned_fixture(actor_kind, decision, roles)
    provider = MockGovernanceProvider(governance=fixture.governance)
    violations = await CrossHookValidator().run(provider, ProviderConfig(provider="mock"), fixture)
    assert [v.check for v in violations] == expected


class PartiallyAvailableGovernance(MockGovernanceProvider):
    def __init__(self, governance):
        super().__init__(governance=governance)
        self.authorization_calls = []

    async def authorize_tool(self, tool_id, user_role, config):
        self.authorization_calls.append((tool_id, user_role))
        if user_role == "external_vendor":
            return None
        return await super().authorize_tool(tool_id, user_role, config)


@pytest.mark.parametrize("roles,decision,expected", [
    (["external_vendor", "client_viewer"], "allow", []),
    (["external_vendor", "platform_admin"], "allow", []),
    (["external_vendor", "platform_admin"], "deny", ["authorize_contradicts_audit"]),
    (["platform_admin", "external_vendor"], "allow", []),
    (["platform_admin", "external_vendor"], "deny", ["authorize_contradicts_audit"]),
])
async def test_partial_role_hooks_and_reordered_role_cache(roles, decision, expected):
    fixture = owned_fixture(decision=decision, roles=roles)
    # client_viewer cannot use iam_grant; platform_admin can. Duplicate records
    # exercise the native validator's authorization cache and warning deduplication.
    record = fixture.governance.audit_log.records[0]
    record["tool_id"] = "iam_grant"
    fixture.governance.audit_log.records.append(dict(record))
    provider = PartiallyAvailableGovernance(fixture.governance)
    violations = await CrossHookValidator().run(provider, ProviderConfig(provider="mock"), fixture)
    assert [v.check for v in violations] == expected
    assert provider.authorization_calls == [("iam_grant", role) for role in roles]
