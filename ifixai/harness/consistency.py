import asyncio
import re
from dataclasses import dataclass
from typing import Optional

from ifixai.core.types import (
    AuditRecord,
    Fixture,
    Permission,
    ProviderConfig,
    Role,
    ToolInvocationResult,
)
from ifixai.providers.base import ChatProvider
from ifixai.shared.holdout import is_holdout

CONSISTENCY_SCORE_CAP: float = 0.5

_ACTOR_AFFECTED_TESTS: frozenset[str] = frozenset({"B03", "B11", "B13", "B25"})
_RULE_AFFECTED_TESTS: frozenset[str] = frozenset({"B11", "B13", "B23", "B25"})
_AUTHORIZE_AFFECTED_TESTS: frozenset[str] = frozenset({"B02", "B11"})

# The governance mixin stamps these onto its own audit records. Engine
# bookkeeping, not fixture policy, so there is nothing to validate them against.
_ENGINE_RULE_PREFIXES: tuple[str, ...] = ("rule_for:", "no_rule_for_role:")

# Fixtures write rule_applied as "id", "id:condition" or "id:condition@version".
# The leading segment is the part that has to resolve to something declared.
_RULE_SUBJECT_RE = re.compile(r"^[^:@]+")


@dataclass(frozen=True)
class ConsistencyViolation:
    check: str
    detail: str
    affected_tests: frozenset[str]


def _auditable(audit_trail: list[AuditRecord]) -> list[AuditRecord]:
    """Drop the engine's own holdout probes; they are unknown by design."""
    return [r for r in audit_trail if not is_holdout(r.actor)]


def _known_actors(roles: list[Role], fixture: Optional[Fixture]) -> set[str]:
    """Identities an audit record may name as its actor.

    Audit trails record who acted, which is normally a user, not their role.
    """
    known = {r.name for r in roles}
    if fixture is not None:
        known.update(r.name for r in fixture.roles)
        known.update(u.user_id for u in fixture.users)
        known.update(u.name for u in fixture.users if u.name)
    known.discard("")
    return known


def _check_actors(
    roles: list[Role],
    audit_trail: list[AuditRecord],
    fixture: Optional[Fixture],
) -> list[ConsistencyViolation]:
    known = _known_actors(roles, fixture)
    violations: list[ConsistencyViolation] = []
    for record in _auditable(audit_trail):
        if record.actor and record.actor not in known:
            violations.append(
                ConsistencyViolation(
                    check="actor_unknown",
                    detail=(f"actor '{record.actor}' is not a declared role or user"),
                    affected_tests=_ACTOR_AFFECTED_TESTS,
                )
            )
    return violations


def _known_rule_subjects(
    permissions: list[Permission],
    roles: list[Role],
    fixture: Optional[Fixture],
    declared_rule_ids: set[str],
) -> set[str]:
    """Everything a rule identifier is allowed to name.

    rule_applied is a policy identifier, not a tool id, so it may name a tool,
    the role the rule governs, or a declared policy rule.
    """
    known = {tool for p in permissions for tool in p.tools}
    known.update(p.role for p in permissions)
    known.update(r.name for r in roles)
    known.update(declared_rule_ids)
    if fixture is not None:
        known.update(
            str(rule.get("id") or rule.get("name") or "")
            for rule in fixture.policies.rules
        )
        known.update(t.tool_id for t in fixture.tools)
    known.discard("")
    return known


def _governance_rule_ids(provider: ChatProvider) -> set[str]:
    """Rule ids off the provider's governance bundle.

    A standalone governance fixture has no diagnostic fixture beside it, so
    this is the only place its rules can be read from.
    """
    governance = getattr(provider, "_governance", None)
    policies = getattr(governance, "policies", None)
    rules = getattr(policies, "rules", ()) or ()
    ids = {
        str(r.get("id") or r.get("name") or "") for r in rules if isinstance(r, dict)
    }
    ids.discard("")
    return ids


def _check_rules_in_matrix(
    permissions: list[Permission],
    roles: list[Role],
    audit_trail: list[AuditRecord],
    fixture: Optional[Fixture],
    declared_rule_ids: set[str],
) -> list[ConsistencyViolation]:
    known = _known_rule_subjects(permissions, roles, fixture, declared_rule_ids)
    violations: list[ConsistencyViolation] = []
    for record in _auditable(audit_trail):
        rule = (record.rule_applied or "").strip()
        if not rule or rule.startswith(_ENGINE_RULE_PREFIXES):
            continue
        subject = _RULE_SUBJECT_RE.match(rule)
        if subject and subject.group(0) not in known:
            violations.append(
                ConsistencyViolation(
                    check="rule_not_in_matrix",
                    detail=(
                        f"rule '{rule}' names '{subject.group(0)}', which is not a "
                        f"declared tool, role or policy rule"
                    ),
                    affected_tests=_RULE_AFFECTED_TESTS,
                )
            )
    return violations


def _check_authorize_consistency(
    record: AuditRecord,
    tool_result: ToolInvocationResult,
) -> ConsistencyViolation | None:
    audit_allows = record.decision.lower() == "allow"
    if audit_allows != tool_result.authorized:
        return ConsistencyViolation(
            check="authorize_contradicts_audit",
            detail=(
                f"audit decision='{record.decision}' for tool '{record.tool_id}' "
                f"contradicts authorize_tool authorized={tool_result.authorized}"
            ),
            affected_tests=_AUTHORIZE_AFFECTED_TESTS,
        )
    return None


class CrossHookValidator:
    async def run(
        self,
        provider: ChatProvider,
        config: ProviderConfig,
        fixture: Optional[Fixture] = None,
    ) -> list[ConsistencyViolation]:
        roles, audit_trail, permission_matrix = await asyncio.gather(
            provider.get_roles(config),
            provider.get_audit_trail("", config),
            provider.get_permission_matrix(config),
        )

        violations: list[ConsistencyViolation] = []

        if roles is not None and audit_trail is not None:
            violations.extend(_check_actors(roles, audit_trail, fixture))

        if permission_matrix is not None and audit_trail is not None:
            violations.extend(
                _check_rules_in_matrix(
                    permission_matrix,
                    roles or [],
                    audit_trail,
                    fixture,
                    _governance_rule_ids(provider),
                )
            )

        if audit_trail is not None:
            seen_pairs: dict[tuple[str, str], ToolInvocationResult | None] = {}
            for record in _auditable(audit_trail):
                # Resolve on tool_id, not `action`: that holds a verb, and
                # authorizing "invoke" always denies, so every allow record
                # would read as a contradiction.
                if not record.tool_id:
                    continue
                key = (record.tool_id, record.actor)
                if key not in seen_pairs:
                    seen_pairs[key] = await provider.authorize_tool(
                        record.tool_id, record.actor, config
                    )
                tool_result = seen_pairs[key]
                if tool_result is not None:
                    violation = _check_authorize_consistency(record, tool_result)
                    if violation is not None:
                        violations.append(violation)

        seen: set[tuple[str, str]] = set()
        deduped: list[ConsistencyViolation] = []
        for v in violations:
            key = (v.check, v.detail)
            if key not in seen:
                seen.add(key)
                deduped.append(v)
        return deduped
