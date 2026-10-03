"""Infer admin aliases without granting destructive access to lookalike labels."""

from unittest.mock import AsyncMock

import pytest

import ifixai.core.discovery as discovery
from ifixai.core.types import (
    ContextProfile,
    IndustryContext,
    ProviderCapabilities,
    ProviderConfig,
    Role,
    ToolInfo,
    UseCase,
)
from ifixai.quick_build import (
    QuickBuildContext,
    generate_fixture_from_context,
    generate_fixture_from_profile,
)

_ADMIN_ROLES = ["admin", "System Administrator", "Admins", "IT Admin",
                "Store Admin", "Administrators", "Database Administrator", "Account Admin"]
_OTHER_ROLES = ["non-admin", "admin-assistant", "IT Admin Assistant", "user",
                "non Account Admin", "Nonprofit Administrator"]


def _assert_permissions(permissions):
    tools_by_role = {item.role: set(item.tools) for item in permissions}
    for role in _ADMIN_ROLES:
        assert "delete_record" in tools_by_role[role], role
    for role in _OTHER_ROLES:
        assert "delete_record" not in tools_by_role[role], role


def test_quick_build_preserves_admin_aliases_without_granting_lookalikes():
    fixture = generate_fixture_from_context(QuickBuildContext(
        tool_names=["read_record", "delete_record"],
        role_names=_ADMIN_ROLES + _OTHER_ROLES,
    ))
    _assert_permissions(fixture.permissions)


def test_profile_build_preserves_admin_aliases_without_granting_lookalikes():
    profile = ContextProfile(
        industry=IndustryContext(name="General", id="general"),
        use_case=UseCase(name="Assistant", id="assistant"),
        system_name="Test Assistant",
        actions=["read_record", "delete_record"],
        user_types=_ADMIN_ROLES + _OTHER_ROLES,
    )
    _assert_permissions(generate_fixture_from_profile(profile).permissions)


@pytest.mark.asyncio
async def test_discovery_fallback_preserves_admin_aliases_without_granting_lookalikes(
    monkeypatch,
):
    monkeypatch.setattr(
        discovery, "detect_capabilities",
        AsyncMock(return_value=ProviderCapabilities(has_tool_calling=True)),
    )
    provider = AsyncMock()
    provider.list_tools.return_value = [
        ToolInfo(tool_id="delete_record", name="delete_record", risk_level="critical")
    ]
    provider.get_roles.return_value = [
        Role(name=name, role_id=name) for name in _ADMIN_ROLES + _OTHER_ROLES
    ]
    provider.get_permission_matrix.return_value = None

    result = await discovery.discover_system(provider, ProviderConfig(provider="http"))
    assert result.success
    _assert_permissions(result.permissions)
