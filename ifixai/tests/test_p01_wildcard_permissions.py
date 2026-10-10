"""Owned fixture-backed provider; public P01 API, no external service claims."""
import pytest

from ifixai.core.runner import run_single
from ifixai.core.types import (
    DataSource,
    Fixture,
    FixtureMetadata,
    Permission,
    ProviderCapabilities,
    ProviderConfig,
    TestStatus,
    Tool,
    ToolInvocationResult,
    User,
)
from ifixai.providers.base import ChatProvider


class OwnedPermissionProvider(ChatProvider):
    def __init__(self, grants, allow_reader=False):
        self.grants = grants
        self.calls = []
        self.allow_reader = allow_reader

    async def send_message(self, messages, config):
        return 'Owned structural fixture'

    async def authorize_tool(self, tool_id, user_role, config):
        self.calls.append((user_role, tool_id))
        allowed = tool_id == 'delete_store' and (
            (user_role == 'admin' and ('*' in self.grants or tool_id in self.grants))
            or (self.allow_reader and user_role == 'reader')
        )
        return ToolInvocationResult(authorized=allowed, executed=False, policy_rule='owned policy')


@pytest.mark.parametrize('grants,allow_reader', [(['*'], False), (['delete_store'], False), (['*', 'read_store'], False), (['*'], True)])
async def test_public_p01_excludes_wildcard_authorized_roles(grants, allow_reader):
    fixture = Fixture(
        metadata=FixtureMetadata(name='owned permissions'),
        users=[User(user_id=role, name=role, roles=[role]) for role in ['admin', 'reader', 'viewer', 'auditor', 'guest']],
        permissions=[Permission(role='admin', tools=grants)],
        tools=[Tool(tool_id='delete_store', name='Delete store', risk_level='critical')],
        data_sources=[DataSource(source_id='store', name='Store', classification='critical')],
    )
    provider = OwnedPermissionProvider(grants, allow_reader)
    result = await run_single('P01', provider, ProviderConfig(provider='http'), fixture,
                              capabilities=ProviderCapabilities(has_authorization=True))
    assert result.status == (TestStatus.FAIL if allow_reader else TestStatus.PASS)
    if not allow_reader:
        assert result.score == 1.0
    assert not any(e.test_case_id.startswith('P01-probe-admin-') for e in result.evidence)
    assert any(role == 'admin' for role, _ in provider.calls), 'authorized positive control still runs'
    assert any(e.test_case_id.startswith('P01-control-') for e in result.evidence)
