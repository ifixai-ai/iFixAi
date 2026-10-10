from ifixai.core.discovery import generate_test_cases
from ifixai.core.types import Permission, Role, Tool


def test_wildcard_permissions_generate_real_allow_cases():
    cases = generate_test_cases(
        [Role(name="admin")],
        [Permission(role="admin", tools=["*"])],
        [Tool(tool_id="read", name="Read"), Tool(tool_id="delete", name="Delete")],
    )
    assert {(case.tool_id, case.expected_result) for case in cases} == {
        ("read", "allow"),
        ("delete", "allow"),
    }
    assert all(case.test == "B01" for case in cases)
