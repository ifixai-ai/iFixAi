import asyncio

import pytest

from ifixai.core import connection
from ifixai.core.types import ProviderConfig
from ifixai.providers.base import ChatProvider


class HungCapabilities(ChatProvider):
    async def send_message(self, messages, config):
        return "Hello"

    async def list_tools(self, config):
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_connection_probe_bounds_capability_discovery(monkeypatch):
    monkeypatch.setattr(connection, "CONNECTION_TEST_TIMEOUT", 0.01)
    result = await asyncio.wait_for(
        connection.test_connection(HungCapabilities(), ProviderConfig(provider="mock")),
        timeout=0.2,
    )
    assert result.success
    assert result.capabilities is None
