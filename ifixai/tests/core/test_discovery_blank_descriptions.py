import asyncio
import json

import pytest

from ifixai.core.discovery import discover_fixture, discover_system
from ifixai.core.types import ProviderConfig
from ifixai.providers.governance_fixture import GovernanceFixture
from ifixai.providers.resolver import resolve_provider, wrap_with_governance


@pytest.mark.parametrize('discover', [discover_fixture, discover_system])
@pytest.mark.parametrize('description,risk,expected_risk', [('', '', 'medium'), ('read owned document', '', 'low'), ('   ', '', 'medium'), ('\t\n', '', 'medium'), ('   ', 'high', 'high')])
def test_native_declared_governance_discovery(tmp_path,description,risk,expected_risk,discover):
    import yaml
    path=tmp_path/'governance.yaml'
    path.write_text(yaml.safe_dump({'tools':[{'tool_id':'owned-read','name':'read_document','description':description,'risk_level':risk}]}))
    governance=GovernanceFixture.load(str(path))
    provider=wrap_with_governance(resolve_provider('mock'),governance)
    fixture=asyncio.run(discover(provider,ProviderConfig(provider='mock')))
    print(json.dumps({'description':description,'tools':[v.model_dump() for v in fixture.tools]}))
    assert len(fixture.tools)==1
    assert fixture.tools[0].tool_id=='owned-read'
    assert fixture.tools[0].risk_level==expected_risk
