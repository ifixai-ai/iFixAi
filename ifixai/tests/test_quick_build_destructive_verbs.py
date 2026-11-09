"""Quick Build must treat every data-destroying verb like "delete"."""

import pytest

from ifixai.inspections.probe_context import _safest_tool, is_safe_to_ask
from ifixai.quick_build import QuickBuildContext, generate_fixture_from_context


@pytest.mark.parametrize("tool_id", [
    "purge_records", "wipe_device", "erase_backup", "truncate_table",
    "destroy_volume", "terminate_session",
])
def test_destructive_tool_is_a_critical_delete_kept_from_ordinary_roles(tool_id):
    fixture = generate_fixture_from_context(QuickBuildContext(
        tool_names=[tool_id, "export_report"], role_names=["agent", "admin"],
    ))
    tool = next(t for t in fixture.tools if t.tool_id == tool_id)
    assert (tool.category, tool.risk_level) == ("delete", "critical")
    assert not is_safe_to_ask(tool)
    granted = {p.role: set(p.tools) for p in fixture.permissions}
    assert tool_id not in granted["agent"]
    assert tool_id in granted["admin"]
    # The benign control never asks the ordinary role to destroy data.
    assert _safest_tool(fixture, granted["agent"]) != tool_id


def test_lookalike_names_are_not_destructive():
    fixture = generate_fixture_from_context(QuickBuildContext(
        tool_names=["swipe_card", "search_records"], role_names=["agent"],
    ))
    assert {t.tool_id: t.category for t in fixture.tools}["swipe_card"] != "delete"
