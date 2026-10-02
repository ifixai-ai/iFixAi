"""Generated case IDs must stay attached to the same tool across runs."""

from ifixai.core.discovery import generate_test_cases
from ifixai.core.types import Permission, Role, Tool


def test_generated_case_ids_follow_stable_tool_order():
    roles = [Role(name="member", role_id="member")]
    tools = [
        Tool(tool_id=tool_id, name=tool_id)
        for tool_id in (
            "tool_zeta", "tool_beta", "tool_epsilon", "tool_alpha",
            "tool_delta", "tool_gamma", "tool_eta", "tool_theta",
        )
    ]
    permissions = [Permission(
        role="member",
        tools=["tool_zeta", "tool_gamma", "tool_alpha", "tool_delta"],
    )]

    cases = generate_test_cases(roles, permissions, tools)

    assert [(case.test_id, case.test, case.tool_id) for case in cases] == [
        ("tc-001", "B01", "tool_alpha"),
        ("tc-002", "B01", "tool_delta"),
        ("tc-003", "B01", "tool_gamma"),
        ("tc-004", "B01", "tool_zeta"),
        ("tc-005", "B08", "tool_beta"),
        ("tc-006", "B08", "tool_epsilon"),
        ("tc-007", "B08", "tool_eta"),
        ("tc-008", "B08", "tool_theta"),
    ]
