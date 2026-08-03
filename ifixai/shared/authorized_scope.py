"""Pick work a role is actually allowed to do.

Inspections that hand the system a legitimate session objective (B18, B21)
have to build it from a tool and data source the acting role can reach.
Choosing them independently pairs a role with a tool it cannot call, so a
correctly governed system refuses, and the inspection scores the refusal as
goal drift: the score then moves opposite to the behaviour.
"""

from ifixai.core.types import Fixture


def _rotate(items: list, index: int, fallback):
    return items[index % len(items)] if items else fallback


def authorized_tool_name(fixture: Fixture, role: str, index: int) -> str:
    """A tool `role` is permitted to call, rotating by index for diversity."""
    allowed = {
        tool_id
        for perm in fixture.permissions
        if perm.role == role
        for tool_id in perm.tools
    }
    candidates = [t for t in fixture.tools if t.tool_id in allowed or "*" in allowed]
    # No permission entry for this role means the fixture declares no
    # restriction to honour, so any tool is fair game.
    if not candidates:
        candidates = list(fixture.tools)
    tool = _rotate(candidates, index, None)
    return tool.name if tool else "general query"


def accessible_data_source_name(fixture: Fixture, role: str, index: int) -> str:
    """A data source `role` can read, rotating by index for diversity."""
    candidates = [
        s
        for s in fixture.data_sources
        if not s.accessible_by_roles or role in s.accessible_by_roles
    ]
    if not candidates:
        candidates = list(fixture.data_sources)
    source = _rotate(candidates, index, None)
    return source.name if source else "primary data store"
