import pytest

from ifixai.core.types import Fixture, Permission, Tool
from ifixai.shared.authorized_scope import authorized_tool_name


@pytest.mark.parametrize("tools", [[], ["missing_tool"]])
def test_explicit_permission_without_usable_tool_avoids_forbidden_objective(tools):
    fixture = Fixture.model_construct(
        permissions=[Permission(role="reader", tools=tools)],
        tools=[Tool(tool_id="delete", name="Delete records")],
    )
    assert authorized_tool_name(fixture, "reader", 0) == "general query"


def test_absent_permission_retains_unrestricted_fallback():
    fixture = Fixture.model_construct(
        permissions=[], tools=[Tool(tool_id="read", name="Read records")]
    )
    assert authorized_tool_name(fixture, "reader", 0) == "Read records"
