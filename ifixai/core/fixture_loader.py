

import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from ifixai.core.types import (
    DataSource,
    Fixture,
    FixtureMetadata,
    Permission,
    Policy,
    Regulation,
    Role,
    TestCase,
    Tool,
    User,
)
from ifixai.providers.governance_fixture import GovernanceFixture

_SCHEMA_PATH = Path(__file__).parent.parent / "fixtures" / "schema.json"
_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

class FixtureValidationError(Exception):

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []

def load_schema() -> dict[str, Any]:
    with open(_SCHEMA_PATH) as fh:
        return json.load(fh)  # type: ignore[no-any-return]

def resolve_fixture_path(name_or_path: str | Path) -> Path:
    path = Path(name_or_path)
    if path.exists():
        return path

    builtin_path = _FIXTURES_DIR / str(name_or_path) / "fixture.yaml"
    if builtin_path.exists():
        return builtin_path

    raise FileNotFoundError(
        f"Fixture not found: '{name_or_path}'. "
        f"Provide a file path or a built-in name from: {list_fixture_names()}"
    )

def list_fixture_names() -> list[str]:
    if not _FIXTURES_DIR.exists():
        return []
    return sorted(
        d.name
        for d in _FIXTURES_DIR.iterdir()
        if d.is_dir() and (d / "fixture.yaml").exists()
    )

def load_fixture(path: str | Path) -> Fixture:
    fixture_path = resolve_fixture_path(path)

    raw = _read_fixture_file(fixture_path)
    raw = _normalize_fixture_format(raw)
    _validate_against_schema(raw, fixture_path)
    return _parse_fixture(raw)

def validate_fixture(path: str | Path) -> list[str]:
    try:
        fixture_path = resolve_fixture_path(path)
    except FileNotFoundError as exc:
        return [str(exc)]

    raw = _read_fixture_file(fixture_path)
    raw = _normalize_fixture_format(raw)
    schema = load_schema()
    validator = jsonschema.Draft7Validator(schema)
    return [error.message for error in validator.iter_errors(raw)]

def list_test_coverage(fixture: Fixture) -> dict[str, int]:
    coverage: dict[str, int] = {}
    for tc in fixture.test_cases:
        coverage[tc.test] = coverage.get(tc.test, 0) + 1
    return coverage

CATEGORY_NORMALIZATION: dict[str, str] = {
    "information_access": "read",
    "data_modification": "write",
    "data_management": "write",
    "record_modification": "write",
    "system_management": "infrastructure",
    "system_administration": "infrastructure",
    "system_configuration": "infrastructure",
    "data_analysis": "read",
    "reporting": "read",
    "financial_management": "write",
    "financial_operations": "write",
    "compliance": "audit",
    "compliance_management": "audit",
    "compliance_monitoring": "audit",
    "communication": "write",
    "document_management": "write",
    "case_management": "write",
    "resource_management": "write",
    "risk_management": "audit",
    "customer_management": "write",
    "property_management": "write",
    "project_management": "write",
    "hr_management": "write",
    "training_management": "write",
    "policy_management": "write",
    "incident_management": "write",
    "security_management": "infrastructure",
    "strategic_planning": "write",
    "performance_monitoring": "audit",
    "budget_management": "write",
    "vendor_management": "write",
    "operations": "write",
    "administration": "infrastructure",
    "execution": "write",
    "analysis": "read",
    "monitoring": "audit",
    "investigation": "read",
    "transaction": "write",
    "assessment": "read",
    "processing": "write",
    "review": "read",
    "scheduling": "write",
    "tracking": "read",
    "verification": "audit",
}

CLASSIFICATION_NORMALIZATION: dict[str, str] = {
    "highly_confidential": "confidential",
    "top_secret": "restricted",
    "secret": "restricted",
    "sensitive": "confidential",
    "private": "confidential",
}

def _normalize_fixture_format(raw: dict[str, Any]) -> dict[str, Any]:
    if "metadata" in raw:
        return raw

    normalized = dict(raw)

    normalized["metadata"] = {
        "name": raw.get("name", "unknown"),
        "version": raw.get("version", "1.0"),
        "domain": raw.get("name", "unknown"),
        "description": raw.get("description", ""),
    }

    tenant = raw.get("tenant", {})
    tenant_roles = tenant.get("roles", [])
    normalized["roles"] = [
        {"name": r.get("id", r.get("name", "")), "description": r.get("description", "")}
        for r in tenant_roles
    ]

    if "users" not in raw:
        normalized["users"] = [
            {
                "user_id": f"user_{i+1:03d}",
                "name": r.get("name", r.get("id", "")),
                "roles": [r.get("id", r.get("name", ""))],
            }
            for i, r in enumerate(tenant_roles)
        ]

    if "tools" in raw:
        normalized_tools = []
        for t in raw["tools"]:
            tool = dict(t)
            if "tool_id" not in tool and "id" in tool:
                tool["tool_id"] = tool.pop("id")
            cat = tool.get("category", "read")
            tool["category"] = CATEGORY_NORMALIZATION.get(cat, cat)
            normalized_tools.append(tool)
        normalized["tools"] = normalized_tools

    if "permissions" in raw:
        perm_raw = raw["permissions"]
        normalized_perms = []
        if perm_raw and isinstance(perm_raw[0], dict):
            if "role_id" in perm_raw[0]:
                role_tools: dict[str, list[str]] = {}
                for p in perm_raw:
                    if p.get("allowed", True):
                        role_id = p["role_id"]
                        role_tools.setdefault(role_id, []).append(p["tool_id"])
                for role_id, tools in role_tools.items():
                    normalized_perms.append({"role": role_id, "tools": tools})
            else:
                normalized_perms = perm_raw
        normalized["permissions"] = normalized_perms

    if "data_sources" in raw:
        normalized_ds = []
        for ds in raw["data_sources"]:
            d = dict(ds)
            if "source_id" not in d and "id" in d:
                d["source_id"] = d.pop("id")
            cls = d.get("classification", "internal")
            d["classification"] = CLASSIFICATION_NORMALIZATION.get(cls, cls)
            normalized_ds.append(d)
        normalized["data_sources"] = normalized_ds

    tc_raw = raw.get("test_cases", [])
    if isinstance(tc_raw, dict):
        flat_cases: list[dict[str, Any]] = []
        test_map = {
            "b01_tool_governance": "B01",
            "b04_deterministic_override": "B04",
            "b07_hallucination_rate": "B07",
            "b08_privilege_escalation": "B08",
            "b09_policy_violation": "B09",
            "b12_prompt_injection": "B12",
            "b22_decision_reproducibility": "B22",
            "b27_session_integrity": "B27",
        }
        for key, cases in tc_raw.items():
            test_id = test_map.get(key, f"ifixai-{key.upper()[:3]}")
            for tc in cases:
                flat_cases.append({
                    "test_id": tc.get("id", tc.get("test_id", "")),
                    "test": test_id,
                    "scenario": tc.get("scenario", tc.get("reasoning", "")),
                    "expected_result": tc.get("expected", tc.get("expected_result", "")),
                    "user_role": tc.get("user_role", ""),
                    "tool_id": tc.get("tool_id", tc.get("tool", "")),
                    "reasoning": tc.get("reasoning", ""),
                })
        normalized["test_cases"] = flat_cases
    elif not tc_raw:
        normalized["test_cases"] = []

    regs_raw = raw.get("regulations", [])
    if isinstance(regs_raw, list):
        normalized_regs = []
        for reg in regs_raw:
            r = dict(reg)
            if "required_tests" not in r:
                r["required_tests"] = []
            normalized_regs.append(r)
        normalized["regulations"] = normalized_regs
    else:
        normalized["regulations"] = []

    policies_raw = raw.get("policies", {})
    if isinstance(policies_raw, list):
        normalized["policies"] = {
            "confidence_threshold": 0.85,
            "citation_required": True,
            "override_allowed_roles": [],
            "audit_logging": "required",
        }
    elif not policies_raw:
        normalized["policies"] = {}

    return normalized

def _read_fixture_file(path: Path) -> dict[str, Any]:
    with open(path) as fh:
        text = fh.read()

    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(text)
    elif path.suffix == ".json":
        data = json.loads(text)
    else:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            data = json.loads(text)

    if not isinstance(data, dict):
        raise FixtureValidationError(
            f"Fixture must be a mapping, got {type(data).__name__}"
        )
    return data  # type: ignore[return-value]

def _validate_against_schema(
    raw: dict[str, Any],
    path: Path,
) -> None:
    schema = load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(raw))
    if errors:
        messages = [e.message for e in errors]
        raise FixtureValidationError(
            f"Fixture {path.name} failed validation with {len(errors)} error(s)",
            errors=messages,
        )

def _parse_fixture(raw: dict[str, Any]) -> Fixture:
    metadata_raw = raw["metadata"]
    metadata = FixtureMetadata(
        name=metadata_raw["name"],
        version=metadata_raw["version"],
        domain=metadata_raw["domain"],
        description=metadata_raw.get("description", ""),
        timestamp=metadata_raw.get("timestamp"),
    )

    roles = [
        Role(name=r["name"], description=r.get("description", ""))
        for r in raw.get("roles", [])
    ]

    users = [
        User(
            user_id=u["user_id"],
            name=u["name"],
            roles=u.get("roles", []),
        )
        for u in raw.get("users", [])
    ]

    tools = [
        Tool(
            tool_id=t["tool_id"],
            name=t["name"],
            category=t["category"],
            risk_level=t["risk_level"],
            description=t.get("description", ""),
            tool_behavior=t.get("tool_behavior", ""),
        )
        for t in raw.get("tools", [])
    ]

    permissions = [
        Permission(role=p["role"], tools=p.get("tools", []))
        for p in raw.get("permissions", [])
    ]

    data_sources = [
        DataSource(
            source_id=ds["source_id"],
            name=ds["name"],
            classification=ds["classification"],
            source_types=ds.get("source_types", []),
        )
        for ds in raw.get("data_sources", [])
    ]

    policies_raw = raw.get("policies", {})
    if not isinstance(policies_raw, dict):
        policies_raw = {}
    policies = Policy.model_validate(policies_raw)

    regulations = [
        Regulation(
            name=reg["name"],
            required_tests=reg.get("required_tests", []),
        )
        for reg in raw.get("regulations", [])
    ]

    test_cases = [
        TestCase(
            test_id=tc["test_id"],
            test=tc["test"],
            scenario=tc["scenario"],
            expected_result=tc["expected_result"],
            user_role=tc.get("user_role", ""),
            tool_id=tc.get("tool_id", ""),
            reasoning=tc.get("reasoning", ""),
            metadata=tc.get("metadata", {}),
        )
        for tc in raw.get("test_cases", [])
    ]

    governance_raw = raw.get("governance")
    governance: GovernanceFixture | None
    if isinstance(governance_raw, dict):
        # `governance: { synthesize: true }` is a deferred marker -- the CLI
        # detects the absence of `policies` and synthesizes from the
        # diagnostic body. Anything else is parsed as a full fixture.
        if governance_raw.get("synthesize") is True and "policies" not in governance_raw:
            governance = None
        else:
            governance = GovernanceFixture.model_validate(governance_raw)
    else:
        governance = None
    synthesize_governance_flag = (
        isinstance(governance_raw, dict)
        and governance_raw.get("synthesize") is True
        and governance is None
    )

    fixture_obj = Fixture(
        metadata=metadata,
        roles=roles,
        users=users,
        tools=tools,
        permissions=permissions,
        data_sources=data_sources,
        policies=policies,
        regulations=regulations,
        test_cases=test_cases,
        governance=governance,
        governance_source="explicit" if governance is not None else None,
        escalation_triggers=raw.get("escalation_triggers", []),
        expected_escalation_channels=raw.get("expected_escalation_channels", []),
        high_risk_actions=raw.get("high_risk_actions", []),
        sensitive_data_classes=raw.get("sensitive_data_classes", []),
        system_purpose=raw.get("system_purpose", ""),
        primary_objective_template=raw.get("primary_objective_template", ""),
    )

    if synthesize_governance_flag:
        # Local import to avoid circular dep: synthesis reads from Fixture.
        from ifixai.core.governance_synthesis import synthesize_governance

        fixture_obj = fixture_obj.model_copy(
            update={
                "governance": synthesize_governance(fixture_obj),
                "governance_source": "synth",
            },
        )

    return fixture_obj
