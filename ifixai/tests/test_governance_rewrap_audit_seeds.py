import pytest

from ifixai.core.types import ProviderConfig
from ifixai.providers.governance_fixture import GovernanceFixture, SeededAuditRecord
from ifixai.providers.mock_governance import MockGovernanceProvider
from ifixai.providers.resolver import wrap_with_governance


@pytest.mark.asyncio
async def test_replacing_governance_fixture_applies_its_own_audit_seeds():
    first = GovernanceFixture(seed_audit_records=(SeededAuditRecord(actor='first'),))
    second = GovernanceFixture(seed_audit_records=(SeededAuditRecord(actor='second'),))
    provider = MockGovernanceProvider(governance=first)
    config = ProviderConfig(provider='mock')
    assert [record.actor for record in await provider.get_audit_trail('', config)] == ['first']
    assert wrap_with_governance(provider, second) is provider
    assert [record.actor for record in await provider.get_audit_trail('', config)] == ['second']
    wrap_with_governance(provider, second)
    assert [record.actor for record in await provider.get_audit_trail('', config)] == ['second']
