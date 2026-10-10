from ifixai.core.types import DataSource, Fixture, Permission, Role, Tool
from ifixai.shared.authorized_scope import (
    accessible_data_source_name,
    authorized_tool_name,
)


def fixture():
    return Fixture.model_construct(
        roles=[Role(name="Analyst", role_id="analyst")],
        permissions=[Permission(role="analyst", tools=["read"])],
        tools=[
            Tool(tool_id="delete", name="Delete"),
            Tool(tool_id="read", name="Read"),
        ],
        data_sources=[
            DataSource(
                source_id="restricted",
                name="Restricted",
                classification="public",
                accessible_by_roles=["admin"],
            ),
            DataSource(
                source_id="readable",
                name="Readable",
                classification="internal",
                accessible_by_roles=["analyst"],
            ),
        ],
    )


def test_display_role_name_resolves_permission_identity():
    assert authorized_tool_name(fixture(), "Analyst", 0) == "Read"


def test_display_role_name_resolves_reader_identity():
    assert accessible_data_source_name(fixture(), "Analyst", 0) == "Readable"


def test_stable_role_id_remains_supported():
    assert authorized_tool_name(fixture(), "analyst", 0) == "Read"
    assert accessible_data_source_name(fixture(), "analyst", 0) == "Readable"
