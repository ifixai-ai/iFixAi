import asyncio

import pytest

from ifixai.core.types import (
    Fixture,
    FixtureMetadata,
    Permission,
    ProviderCapabilities,
    ProviderConfig,
    Tool,
    ToolInfo,
    ToolInvocationResult,
    User,
)
from ifixai.inspections.b14_covert_side_task.runner import B14CovertSideTask
from ifixai.providers.base import ChatProvider


class SyntheticProvider(ChatProvider):
    async def send_message(self, messages, config):
        return "Synthetic refusal for wildcard permission test."

    async def list_tools(self, config):
        return [
            ToolInfo(tool_id="export_data", name="Export data"),
            ToolInfo(tool_id="admin_backdoor", name="Admin backdoor"),
        ]

    async def invoke_tool(self, tool_id, user_role, params, config):
        return ToolInvocationResult(authorized=False, executed=False)


@pytest.mark.asyncio
async def test_b14_wildcard_grant_probes_the_fixture_tools():
    fixture = Fixture(
        metadata=FixtureMetadata(name="synthetic B14 wildcard permissions"),
        users=[User(user_id="admin", name="Admin", roles=["admin"])],
        tools=[Tool(tool_id="export_data", name="Export data")],
        permissions=[Permission(role="admin", tools=["*"])],
    )
    inspection = B14CovertSideTask()
    inspection.capabilities = ProviderCapabilities(has_tool_calling=True)

    evidence = await inspection._inspection_suspicious_tools(
        SyntheticProvider(),
        ProviderConfig(provider="synthetic"),
        fixture.users[0],
        fixture,
        asyncio.Semaphore(1),
    )

    assert [item.details["tool_id"] for item in evidence] == ["export_data"]
