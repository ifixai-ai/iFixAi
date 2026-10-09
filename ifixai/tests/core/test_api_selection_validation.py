"""Selection mistakes must not silently narrow a public API audit."""

import pytest

from ifixai import api
from ifixai.core.fixture_loader import load_fixture
from ifixai.providers.base import ChatProvider


class SelectionProvider(ChatProvider):
    def __init__(self):
        self.capability_calls = 0
        self.message_calls = 0
        self.closed = False

    async def list_tools(self, config):
        self.capability_calls += 1
        return []

    async def send_message(self, messages, config):
        self.message_calls += 1
        return "ACK"

    async def aclose(self):
        self.closed = True


@pytest.fixture
def fixture_without_governance():
    # Exercise the provided agent's actual hooks instead of fixture composition.
    return load_fixture("default").model_copy(update={"governance": None})


@pytest.mark.asyncio
@pytest.mark.parametrize("test_ids", [{"B999"}, {"B02", "B999"}, ["B02", "B999"]])
async def test_unknown_selection_rejected_before_agent_probes(
    test_ids, fixture_without_governance
):
    provider = SelectionProvider()
    with pytest.raises(ValueError, match="B999"):
        await api.run_selected(test_ids, provider, fixture=fixture_without_governance)
    assert provider.capability_calls == 0
    assert provider.message_calls == 0
    assert provider.closed


@pytest.mark.asyncio
async def test_unknown_single_rejected_before_agent_probes(fixture_without_governance):
    provider = SelectionProvider()
    with pytest.raises(ValueError, match="Unknown test: B999"):
        await api.run_single("B999", provider, fixture=fixture_without_governance)
    assert provider.capability_calls == 0
    assert provider.message_calls == 0
    assert provider.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "selected", "selected_list"])
async def test_valid_selection_keeps_native_inspection_and_teardown(
    mode, fixture_without_governance
):
    provider = SelectionProvider()
    if mode == "single":
        result = await api.run_single("B02", provider, fixture=fixture_without_governance)
        ids = [result.test_id]
    else:
        result = await api.run_selected(
            {"B02"} if mode == "selected" else ["B02"],
            provider, fixture=fixture_without_governance
        )
        ids = [inspection.test_id for inspection in result.test_results]
    assert ids == ["B02"]
    assert provider.capability_calls == 1
    assert provider.closed
