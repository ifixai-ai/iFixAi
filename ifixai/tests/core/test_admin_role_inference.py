"""A role label must not grant destructive access by substring accident."""

from unittest.mock import AsyncMock

import pytest

import ifixai.core.discovery as discovery
from ifixai.core.types import ProviderCapabilities, ProviderConfig, Role, ToolInfo
from ifixai.quick_build import QuickBuildContext, generate_fixture_from_context


def test_quick_build_does_not_make_non_admin_an_administrator():
    fixture = generate_fixture_from_context(QuickBuildContext(
        tool_names=["read_record", "delete_record"],
        role_names=["non-admin", "admin"],
    ))
    permissions = {item.role: set(item.tools) for item in fixture.permissions}

    assert "delete_record" not in permissions["non-admin"]
    assert "delete_record" in permissions["admin"]


@pytest.mark.asyncio
async def test_discovery_fallback_does_not_make_non_admin_an_administrator(monkeypatch):
    monkeypatch.setattr(
        discovery, "detect_capabilities",
        AsyncMock(return_value=ProviderCapabilities(has_tool_calling=True)),
    )
    provider = AsyncMock()
    provider.list_tools.return_value = [
        ToolInfo(tool_id="delete_record", name="delete_record", risk_level="critical")
    ]
    provider.get_roles.return_value = [
        Role(name="non-admin", role_id="non-admin"),
        Role(name="admin", role_id="admin"),
    ]
    provider.get_permission_matrix.return_value = None

    result = await discovery.discover_system(provider, ProviderConfig(provider="http"))
    permissions = {item.role: set(item.tools) for item in result.permissions}

    assert result.success
    assert "delete_record" not in permissions["non-admin"]
    assert "delete_record" in permissions["admin"]
