"""Generated allow/deny cases must use the provider's permission role key."""

from ifixai.core.discovery import generate_test_cases
from ifixai.core.types import Permission, Role, Tool


def test_role_id_permission_is_not_misclassified_as_denied():
    roles = [Role(name="Administrator", role_id="admin")]
    permissions = [Permission(role="admin", tools=["delete_record"])]
    tools = [Tool(tool_id="delete_record", name="Delete record")]

    cases = generate_test_cases(roles, permissions, tools)

    assert len(cases) == 1
    assert cases[0].test == "B01"
    assert cases[0].expected_result == "allow"
    assert cases[0].user_role == "admin"


def test_display_name_permission_still_works():
    roles = [Role(name="Administrator", role_id="admin")]
    permissions = [Permission(role="Administrator", tools=["delete_record"])]
    tools = [Tool(tool_id="delete_record", name="Delete record")]

    cases = generate_test_cases(roles, permissions, tools)

    assert len(cases) == 1
    assert cases[0].test == "B01"
    assert cases[0].user_role == "Administrator"
