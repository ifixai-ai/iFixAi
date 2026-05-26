"""Governance fixture schema — the structural policy surface that providers
expose to tests.

A single `governance.yaml` per run drives every `ChatProvider` structural
method. `per_test` lets a fixture mutate specific fields for one test;
overrides are shallow-merged into `policies` when the provider is queried
in that test's context.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

# B04 trust-contract default probe entries. The policy-bound check needs two
# per_test ids whose authorized_roles differ. Fixtures that do not declare
# their own SSCI-B04 / SSCI-B04-probe entries get these defaults so B04 scores
# 1.0 against any governance-wrapped provider out of the box, without weakening
# the trust contract (a stub that ignores policy_test_id still fails because
# both calls return the same hardcoded rule_applied).
_B04_PRIMARY_TEST_ID = "SSCI-B04"
_B04_PROBE_TEST_ID = "SSCI-B04-probe"
_B04_PROBE_SENTINEL_ROLE = "__b04_probe_role__"


class AuthorizationRule(BaseModel):
    model_config = {"frozen": True}

    role: str
    tools: tuple[str, ...]


class OverridePolicy(BaseModel):
    model_config = {"frozen": True}

    deny_message: str = "Request denied by policy"
    authorized_roles: tuple[str, ...] = Field(default_factory=tuple)


class GovernanceArchitecture(BaseModel):
    model_config = {"frozen": True}

    has_policy_engine: bool = False
    has_audit_log: bool = False
    has_authorization_gateway: bool = False
    components: tuple[str, ...] = Field(default_factory=tuple)
    layers: tuple[str, ...] = Field(default_factory=tuple)
    deterministic_control: bool = False


class TrainingPolicy(BaseModel):
    model_config = {"frozen": True}

    data_retention_days: int = 0
    pii_scrubbed: bool = False
    opt_out_available: bool = False
    signature: str = ""


class RateLimitRule(BaseModel):
    model_config = {"frozen": True}

    requests_per_minute: int = 60


class RiskAssessmentRule(BaseModel):
    model_config = {"frozen": True}

    match: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    band: str = "low"


class RiskAssessmentPolicy(BaseModel):
    model_config = {"frozen": True}

    rules: tuple[RiskAssessmentRule, ...] = Field(default_factory=tuple)
    default: RiskAssessmentRule = Field(
        default_factory=lambda: RiskAssessmentRule(match={}, score=0.5, band="medium")
    )


class SessionPolicy(BaseModel):
    model_config = {"frozen": True}

    cross_user_isolated: bool = False
    context_clearing_on_logout: bool = False


class PoliciesBlock(BaseModel):
    model_config = {"frozen": True}

    authorization: tuple[AuthorizationRule, ...] = Field(default_factory=tuple)
    override: OverridePolicy = Field(default_factory=OverridePolicy)
    governance_architecture: GovernanceArchitecture = Field(
        default_factory=GovernanceArchitecture
    )
    training_policy: TrainingPolicy = Field(default_factory=TrainingPolicy)
    rate_limits: dict[str, RateLimitRule] = Field(default_factory=dict)
    risk_assessment: RiskAssessmentPolicy = Field(default_factory=RiskAssessmentPolicy)
    session: SessionPolicy = Field(default_factory=SessionPolicy)


class AuditLog(BaseModel):
    # Intentionally not frozen: the mock provider appends to `records` as
    # tests exercise `invoke_tool`.
    records: list[dict[str, Any]] = Field(default_factory=list)


class SeededAuditRecord(BaseModel):
    """A pre-stamped audit record used to make B23 self-contained.

    Without seeded records, B23 (policy version traceability) depends on
    B03/B04 having run first to populate `audit_log.records`. Declaring a
    handful of seed records on the fixture removes that ordering coupling.
    """

    model_config = {"frozen": True}

    timestamp: str = ""
    actor: str = ""
    action: str = ""
    decision: str = ""
    rule_applied: str = ""
    reasoning: str = ""


class GovernanceTool(BaseModel):
    model_config = {"frozen": True}

    tool_id: str
    name: str
    description: str = ""
    risk_level: str = "medium"
    parameters: dict[str, Any] = Field(default_factory=dict)


class GovernanceFixture(BaseModel):
    version: str = "1.0.0"
    tools: tuple[GovernanceTool, ...] = Field(default_factory=tuple)
    policies: PoliciesBlock = Field(default_factory=PoliciesBlock)
    audit_log: AuditLog = Field(default_factory=AuditLog)
    seed_audit_records: tuple[SeededAuditRecord, ...] = Field(default_factory=tuple)
    per_test: dict[str, dict[str, Any]] = Field(default_factory=dict)
    responses: dict[str, str] = Field(default_factory=dict)
    default_response: Optional[str] = None

    @model_validator(mode="after")
    def _ensure_b04_probe_defaults(self) -> "GovernanceFixture":
        # Inject B04 probe per_test entries only when both are absent. If the
        # fixture declares either one explicitly, respect the author's intent
        # and leave per_test alone.
        if (
            _B04_PRIMARY_TEST_ID not in self.per_test
            and _B04_PROBE_TEST_ID not in self.per_test
        ):
            base_roles = self.policies.override.authorized_roles
            primary_role = base_roles[0] if base_roles else "default"
            probe_role = (
                f"{_B04_PROBE_SENTINEL_ROLE}_alt"
                if primary_role == _B04_PROBE_SENTINEL_ROLE
                else _B04_PROBE_SENTINEL_ROLE
            )
            self.per_test[_B04_PRIMARY_TEST_ID] = {
                "override": {"authorized_roles": [primary_role]},
            }
            self.per_test[_B04_PROBE_TEST_ID] = {
                "override": {"authorized_roles": [probe_role]},
            }
        return self

    def policies_for(self, test_id: Optional[str]) -> PoliciesBlock:
        """Return the effective policies block for `test_id`.

        Overrides from `per_test[test_id]` are shallow-merged onto the
        base block. When there is no override, returns the base block
        unchanged.
        """
        if not test_id or test_id not in self.per_test:
            return self.policies
        overrides = self.per_test[test_id]
        merged = self.policies.model_dump()
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return PoliciesBlock.model_validate(merged)

    @classmethod
    def load(cls, path: str) -> "GovernanceFixture":
        import yaml

        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.model_validate(data)
