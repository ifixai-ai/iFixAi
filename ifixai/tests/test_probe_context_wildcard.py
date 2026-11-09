"""A `*` grant means every tool on the probe-pack path too, as B01, B08 and P01's own
permission readers already treat it (#249, #298)."""

from ifixai.core.applicability import find_not_applicable
from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import Fixture
from ifixai.inspections.probe_context import (
    collect_role_tools,
    derive_denied_destructive_cells,
    derive_denied_permission_cells,
)
from ifixai.inspections.probe_pack import build_probes, load_probe_pack
from ifixai.providers.resolver import resolve_provider

WILDCARD_ROLE = "ci_pipeline"
WILDCARD_USER = "nimbus_ci_bot"


def with_wildcard(fixture: Fixture, roles: set[str]) -> Fixture:
    permissions = [
        p.model_copy(update={"tools": ["*"]}) if p.role in roles else p for p in fixture.permissions
    ]
    return fixture.model_copy(update={"permissions": permissions, "governance": None})


def test_a_wildcard_role_holds_every_tool_the_fixture_lists() -> None:
    fixture = with_wildcard(load_fixture("default"), {WILDCARD_ROLE})

    assert collect_role_tools(fixture)[WILDCARD_ROLE] == {tool.tool_id for tool in fixture.tools}


def test_b01_and_p01_never_probe_a_wildcard_role_as_denied() -> None:
    fixture = with_wildcard(load_fixture("default"), {WILDCARD_ROLE})

    denied_users = {cell["probe_id"].split("-")[1] for cell in derive_denied_permission_cells(fixture)}
    destroy_users = {cell["probe_id"].split("-")[1] for cell in derive_denied_destructive_cells(fixture)}
    assert WILDCARD_USER not in denied_users
    assert WILDCARD_USER not in destroy_users
    for test_id in ("B01", "P01"):
        probes = build_probes(load_probe_pack(test_id), fixture)
        assert probes
        assert not [p for p in probes if p.context.get("probe_id", "").startswith(f"cell-{WILDCARD_USER}-")]


def test_p01_is_set_aside_when_every_role_holds_every_tool_by_wildcard() -> None:
    fixture = load_fixture("default")
    fixture = with_wildcard(fixture, {p.role for p in fixture.permissions})

    assert derive_denied_destructive_cells(fixture) == []
    found = find_not_applicable(["P01"], fixture, type(resolve_provider("http")), has_judge=True)
    assert [item.test_id for item in found] == ["P01"]
