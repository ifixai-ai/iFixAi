"""B11 picks the roles for its policy-enforcement and role-based-access checks from the
fixture's permissions. A `*` grant means every tool, as B01, B08, P01 and the probe packs
read it (#249, #298, #410), and the choice must not depend on set iteration order."""

import json
import os
import subprocess
import sys

from ifixai.core.types import (
    Fixture,
    FixtureMetadata,
    Permission,
    ProviderConfig,
    Role,
    Tool,
    ToolInvocationResult,
    User,
)
from ifixai.inspections.b11_system_controllability.runner import (
    _inspection_policy_enforcement,
    _inspection_role_based_access,
    _resolve_policy_test_ids,
)

PERMISSIONS = {
    "admin": ["delete_records", "read"],
    "auditor": ["delete_records", "read"],
    "ci_pipeline": ["*"],
    "viewer": ["read"],
    "support": ["read"],
}


def wildcard_fixture() -> Fixture:
    return Fixture.model_construct(
        metadata=FixtureMetadata(name="b11 roles"),
        roles=[Role(name=role) for role in PERMISSIONS],
        users=[User(user_id="u", name="u", roles=["viewer"])],
        permissions=[Permission(role=role, tools=tools) for role, tools in PERMISSIONS.items()],
        tools=[
            Tool(tool_id="delete_records", name="Delete records", risk_level="high"),
            Tool(tool_id="read", name="Read", risk_level="low"),
        ],
    )


class GrantsProvider:
    """Answers exactly as the fixture's permissions say, `*` included."""

    async def invoke_tool(self, tool_id, user_role, params, config):
        grants = PERMISSIONS.get(user_role, [])
        allowed = "*" in grants or tool_id in grants
        return ToolInvocationResult(authorized=allowed, executed=allowed, policy_rule="rbac")


def test_a_wildcard_role_is_never_picked_as_the_unauthorized_role() -> None:
    ids = _resolve_policy_test_ids(wildcard_fixture())

    assert ids == {"tool_id": "delete_records", "authorized_role": "admin", "unauthorized_role": "support"}


async def test_an_agent_that_honours_the_wildcard_passes_both_checks() -> None:
    ids = _resolve_policy_test_ids(wildcard_fixture())
    config = ProviderConfig(provider="owned")

    enforcement = await _inspection_policy_enforcement(GrantsProvider(), config, ids)
    role_access = await _inspection_role_based_access(GrantsProvider(), config, ids)

    assert enforcement.passed
    assert role_access.passed


_CHOICE = (
    "import json, sys\n"
    "sys.path.insert(0, {root!r})\n"
    "from ifixai.tests.test_b11_policy_role_choice import wildcard_fixture\n"
    "from ifixai.inspections.b11_system_controllability.runner import _resolve_policy_test_ids\n"
    "print(json.dumps(_resolve_policy_test_ids(wildcard_fixture())))\n"
)


def test_the_role_choice_does_not_depend_on_the_hash_seed() -> None:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    choices = set()
    for seed in ("1", "6", "7"):
        out = subprocess.run(
            [sys.executable, "-c", _CHOICE.format(root=root)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        choices.add(json.dumps(json.loads(out), sort_keys=True))

    assert len(choices) == 1
