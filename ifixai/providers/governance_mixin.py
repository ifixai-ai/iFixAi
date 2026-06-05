"""Shared structural-method implementation for ChatProvider subclasses.

`GovernanceMixin` reads every structural-method result from an in-memory
`GovernanceFixture` so tests hit a real structural surface instead of
asking the LLM to self-report its own governance.

The mixin is test-aware: callers pass `test_id` via `config.model_extra`,
which lets the pipeline wire per-test overrides without changing every
structural-method signature.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

from ifixai.providers.base import ChatProvider
from ifixai.providers.governance_fixture import GovernanceFixture, PoliciesBlock
from ifixai.core.types import (
    ActionConfirmationRequest,
    AuditRecord,
    ConfidenceReport,
    ConfigurationVersion,
    ConfirmationGateReport,
    DeploymentGateReport,
    DetectionAuditWindow,
    FallbackRoutingReport,
    GovernanceArchitecture,
    OutcomeMetricFeed,
    OutcomeReconciliationReport,
    OverrideReceipt,
    Permission,
    ProviderConfig,
    Role,
    ToolInfo,
    ToolInvocationResult,
    classify_action_gate,
    classify_detection_window,
    classify_outcome_feed,
)


def _test_id(config: ProviderConfig) -> Optional[str]:
    """Extract the test id the pipeline annotated on the config.

    Providers used outside the pipeline get the base policies block.
    """
    extra: dict[str, Any] = config.model_extra or {}
    bid = extra.get("test_id")
    return bid if isinstance(bid, str) else None


class GovernanceMixin(ChatProvider):
    """Structural-method implementations for a `ChatProvider`.

    When `_governance` is `None`, every structural method returns `None`
    so the pipeline marks the inspection `INCONCLUSIVE` rather than
    silently falling back to LLM self-report.

    Inherits from `ChatProvider` so that runtime composition
    (`wrap_with_governance`) produces a class with a single, consistent
    instance layout regardless of which concrete provider is wrapped.
    """

    _governance: Optional[GovernanceFixture]
    _audit_seed_applied: bool = False

    def _policies(self, config: ProviderConfig) -> Optional[PoliciesBlock]:
        if self._governance is None:
            return None
        return self._governance.policies_for(_test_id(config))

    async def list_tools(self, config: ProviderConfig) -> list[ToolInfo] | None:
        if self._governance is None:
            return None
        return [
            ToolInfo(
                tool_id=t.tool_id,
                name=t.name,
                description=t.description,
                parameters=t.parameters,
                risk_level=t.risk_level,
            )
            for t in self._governance.tools
        ]

    async def authorize_tool(
        self,
        tool_id: str,
        user_role: str,
        config: ProviderConfig,
    ) -> ToolInvocationResult | None:
        policies = self._policies(config)
        if policies is None:
            return None
        matching = next(
            (r for r in policies.authorization if r.role == user_role), None
        )
        if matching is None:
            return ToolInvocationResult(
                authorized=False,
                executed=False,
                result=None,
                error=policies.override.deny_message,
                policy_rule=f"no_rule_for_role:{user_role}",
            )
        allowed = "*" in matching.tools or tool_id in matching.tools
        return ToolInvocationResult(
            authorized=allowed,
            executed=False,
            result=None,
            error=None if allowed else policies.override.deny_message,
            policy_rule=f"rule_for:{user_role}",
        )

    async def invoke_tool(
        self,
        tool_id: str,
        user_role: str,
        params: dict,
        config: ProviderConfig,
    ) -> ToolInvocationResult | None:
        if self._governance is None:
            return None
        auth = await self.authorize_tool(tool_id, user_role, config)
        if auth is None:
            return None
        request_id = config.request_id
        if auth.authorized:
            self._governance.audit_log.records.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "actor": user_role,
                    "tool_id": tool_id,
                    "action": "invoke",
                    "decision": "allow",
                    "params": params,
                    "policy_rule": auth.policy_rule,
                    "reason": f"authorized via {auth.policy_rule}",
                    "evidence_attached": json.dumps(
                        {"tool_id": tool_id, "params": params}
                    ),
                    "request_id": request_id,
                }
            )
            return ToolInvocationResult(
                authorized=True,
                executed=True,
                result=f"{tool_id}_ok",
                error=None,
                policy_rule=auth.policy_rule,
            )
        self._governance.audit_log.records.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "actor": user_role,
                "tool_id": tool_id,
                "action": "invoke",
                "decision": "deny",
                "params": params,
                "policy_rule": auth.policy_rule,
                "reason": auth.error or "denied",
                "evidence_attached": json.dumps({"tool_id": tool_id, "params": params}),
                "request_id": request_id,
            }
        )
        return auth

    def _ensure_seeded_audit_records(self) -> None:
        """Splice fixture-declared seed records into the audit log once.

        Idempotent: a sentinel `_audit_seed_applied` flag guards re-entry
        so subsequent inspections within the same run don't double-stamp.
        """
        if self._governance is None:
            return
        if self._audit_seed_applied:
            return
        seeds = self._governance.seed_audit_records
        if seeds:
            self._governance.audit_log.records[:0] = [
                {
                    "timestamp": seed.timestamp,
                    "actor": seed.actor,
                    "action": seed.action,
                    "decision": seed.decision,
                    "policy_rule": seed.rule_applied,
                    "reason": seed.reasoning,
                }
                for seed in seeds
            ]
        self._audit_seed_applied = True

    async def get_audit_trail(
        self,
        request_id: str,
        config: ProviderConfig,
    ) -> list[AuditRecord] | None:
        if self._governance is None:
            return None
        self._ensure_seeded_audit_records()
        records = self._governance.audit_log.records
        if request_id:
            # Untagged records (request_id missing / None) are treated as
            # ambient — they match any query so seeded fixture records keep
            # serving inspections like B23 that scan the full pool. Tagged
            # records only match their own id. Callers that need strict
            # per-iteration isolation (B03) re-filter on AuditRecord.request_id.
            records = [
                rec
                for rec in records
                if not rec.get("request_id") or rec.get("request_id") == request_id
            ]
        return [
            AuditRecord(
                timestamp=rec.get("timestamp", ""),
                actor=rec.get("actor", ""),
                action=rec.get("action", ""),
                decision=rec.get("decision", ""),
                rule_applied=rec.get("policy_rule"),
                reasoning=rec.get("reason", ""),
                evidence_attached=rec.get("evidence_attached", ""),
                request_id=rec.get("request_id"),
            )
            for rec in records
        ]

    async def apply_override(
        self,
        request_id: str,
        config: ProviderConfig,
        intent: str | None = None,
        policy_test_id: str | None = None,
    ) -> OverrideReceipt | None:
        if self._governance is None:
            return None
        # Use policy_test_id when explicitly supplied (e.g. policy-bound probe),
        # otherwise fall back to the test_id annotated on the config.
        effective_test_id = (
            policy_test_id if policy_test_id is not None else _test_id(config)
        )
        policies = self._governance.policies_for(effective_test_id)
        authorized_roles = policies.override.authorized_roles
        primary_role = authorized_roles[0] if authorized_roles else "default"
        # Deterministic decision_id: hash of (request_id, intent or "default")
        intent_key = intent or "default"
        digest = hashlib.sha256(f"{request_id}:{intent_key}".encode()).hexdigest()[:16]
        decision_id = f"override-{digest}"
        if intent == "allow":
            rule_applied = f"operator_override_allow_via_{primary_role}"
        elif intent == "deny":
            rule_applied = f"operator_override_deny_via_{primary_role}"
        else:
            rule_applied = f"operator_override_default_via_{primary_role}"
        return OverrideReceipt(
            decision_id=decision_id,
            rule_applied=rule_applied,
            deterministic=True,
            timestamp=datetime.now(timezone.utc).isoformat(),
            deny_message=policies.override.deny_message if intent == "deny" else None,
        )

    async def get_configuration_version(
        self,
        config: ProviderConfig,
    ) -> ConfigurationVersion | None:
        if self._governance is None:
            return None
        policies = self._policies(config)
        if policies is None:
            return None
        # Canonical JSON of the effective policies block + the fixture's
        # declared version → deterministic digest. Same fixture (same test_id
        # overrides, if any) → same digest across runs.
        canonical = json.dumps(policies.model_dump(), sort_keys=True, default=str)
        digest = hashlib.sha256(
            f"{self._governance.version}|{canonical}".encode("utf-8")
        ).hexdigest()[:16]
        return ConfigurationVersion(
            version=self._governance.version,
            source=f"governance_fixture:sha256:{digest}",
            applied_at=datetime.now(timezone.utc).isoformat(),
        )

    async def get_governance_architecture(
        self,
        config: ProviderConfig,
    ) -> GovernanceArchitecture | None:
        policies = self._policies(config)
        if policies is None:
            return None
        arch = policies.governance_architecture
        return GovernanceArchitecture(
            layers=list(arch.layers),
            deterministic_control=arch.deterministic_control,
            non_llm_components=list(arch.components),
        )

    async def get_confidence(
        self,
        query: str,
        config: ProviderConfig,
    ) -> ConfidenceReport | None:
        """Deterministic, self-consistent reference confidence surface (C02).

        Confidence is derived from sha256(query) → [0,1), so the value is
        byte-stable across runs (no model call). The reference system abstains on
        exactly the queries it scores below the fixture-declared
        `policies.confidence.abstain_below` (default 0.85) and emits a hard value
        otherwise — i.e. it never hard-asserts an answer it scored below its own
        abstain floor. Set `abstain_below` equal to the diagnostic fixture's
        `policies.confidence_threshold` to keep the reference self-consistent with
        C02's partition (a clean 1.0); leaving them mismatched is itself a valid
        scenario C02 surfaces (the reference still passes the 0.80 bar). When no
        governance fixture is declared the method returns None (no self-report
        fallback), and C02 routes the target to INCONCLUSIVE — the structural-path
        contract shared with B02/B04/P08.
        """
        policies = self._policies(config)
        if policies is None:
            return None
        abstain_below = policies.confidence.abstain_below
        digest = hashlib.sha256(query.encode("utf-8")).digest()
        confidence = int.from_bytes(digest[:8], "big") / float(1 << 64)
        if confidence < abstain_below:
            return ConfidenceReport(
                confidence=confidence,
                output_kind="abstain",
                response_text="uncertain — manual check advised",
            )
        return ConfidenceReport(
            confidence=confidence,
            output_kind="hard_value",
            response_text="(confident answer)",
        )

    async def route_to_human(
        self,
        observation: str,
        confidence: float,
        config: ProviderConfig,
    ) -> FallbackRoutingReport | None:
        """Deterministic reference human-fallback routing surface (C05).

        Routes a case to a human when the supplied capability `confidence` is below
        the fixture-declared `policies.fallback.route_below` (default 0.85) and
        auto-completes otherwise — a pure comparison, byte-stable across runs (no
        model call). Set `route_below` equal to the diagnostic fixture's
        `policies.confidence_threshold` to keep the reference self-consistent with
        C05's partition (a clean 1.0); a mismatch still passes the 0.80 bar but is a
        valid scenario C05 surfaces. When no governance fixture is declared the
        method returns None (no fallback), and C05 routes the target to INCONCLUSIVE
        — the structural-path contract shared with B02/B04/P08/C02.
        """
        policies = self._policies(config)
        if policies is None:
            return None
        route_below = policies.fallback.route_below
        if confidence < route_below:
            return FallbackRoutingReport(
                route_target="human",
                input_confidence=confidence,
                response_text="routed to human reviewer — confidence below fallback floor",
            )
        return FallbackRoutingReport(
            route_target="auto_complete",
            input_confidence=confidence,
            response_text="auto-completed — confidence above fallback floor",
        )

    async def reconcile_outcome(
        self,
        feed: OutcomeMetricFeed,
        config: ProviderConfig,
    ) -> OutcomeReconciliationReport | None:
        """Deterministic reference outcome-reconciliation surface (C11).

        Reconciles the supplied feed's realised actuals against its declared target
        using the shared `classify_outcome_feed` ground-truth classifier and the
        fixture-declared `policies.outcome_conformance.drift_tolerance` (default
        0.0) — a pure comparison, byte-stable across runs (no model call). Because
        the reference uses the same classifier the runner authors its probe labels
        from, it scores a clean 1.0 against C11's probe set. When no governance
        fixture is declared the method returns None (no self-report fallback), and
        C11 routes the target to INCONCLUSIVE — the structural-path contract shared
        with B02/B04/P08/C02/C05.
        """
        policies = self._policies(config)
        if policies is None:
            return None
        tolerance = policies.outcome_conformance.drift_tolerance
        outcome = classify_outcome_feed(feed, tolerance)
        return OutcomeReconciliationReport(
            reconciliation_outcome=outcome,
            metric_name=feed.metric_name,
            response_text=f"reconciled {feed.metric_name} → {outcome}",
        )

    async def evaluate_deployment_gate(
        self,
        window: DetectionAuditWindow,
        config: ProviderConfig,
    ) -> DeploymentGateReport | None:
        """Deterministic reference detection-acceptance-gate surface (X04).

        Reconciles the supplied window's MEASURED detection performance against its
        DECLARED spec using the shared `classify_detection_window` ground-truth
        classifier and the fixture-declared `policies.detection_gate.tpr_tolerance` /
        `fp_tolerance` (default 0.0) — a pure comparison, byte-stable across runs (no
        model call). Because the reference uses the same classifier the runner authors
        its probe labels from, it scores a clean 1.0 against X04's probe set. When no
        governance fixture is declared the method returns None (no self-report fallback),
        and X04 routes the target to INCONCLUSIVE — the structural-path contract shared
        with B02/B04/P08/C02/C05/C11.
        """
        policies = self._policies(config)
        if policies is None:
            return None
        gate = policies.detection_gate
        outcome = classify_detection_window(
            window, gate.tpr_tolerance, gate.fp_tolerance
        )
        return DeploymentGateReport(
            gate_outcome=outcome,
            detector_name=window.detector_name,
            response_text=f"acceptance gate for {window.detector_name} → {outcome}",
        )

    async def evaluate_confirmation_gate(
        self,
        request: ActionConfirmationRequest,
        config: ProviderConfig,
    ) -> ConfirmationGateReport | None:
        """Deterministic reference pre-action-confirmation-gate surface (X11).

        Reconciles the supplied action's policy classification with its configured gate setup
        using the shared `classify_action_gate` ground-truth classifier — a pure comparison,
        byte-stable across runs (no model call), and no policy/tolerance field is read (the gate is
        a pure classification of the request). Because the reference uses the same classifier the
        runner authors its probe labels from, it scores a clean 1.0 against X11's probe set. When no
        governance fixture is declared the method returns None (no self-report fallback), and X11
        routes the target to INCONCLUSIVE — the structural-path contract shared with
        B02/B04/P08/C02/C05/C11/X04.
        """
        policies = self._policies(config)
        if policies is None:
            return None
        outcome = classify_action_gate(request)
        return ConfirmationGateReport(
            gate_outcome=outcome,
            action_name=request.action_name,
            response_text=f"confirmation gate for {request.action_name} → {outcome}",
        )

    async def get_roles(
        self,
        config: ProviderConfig,
    ) -> list[Role] | None:
        policies = self._policies(config)
        if policies is None:
            return None
        return [Role(name=rule.role) for rule in policies.authorization]

    async def get_permission_matrix(
        self,
        config: ProviderConfig,
    ) -> list[Permission] | None:
        policies = self._policies(config)
        if policies is None:
            return None
        return [
            Permission(role=rule.role, tools=list(rule.tools))
            for rule in policies.authorization
        ]

    async def get_training_policy(
        self,
        config: ProviderConfig,
    ) -> dict[str, Any] | None:
        policies = self._policies(config)
        if policies is None:
            return None
        return policies.training_policy.model_dump()

    async def get_rate_limit_policy(
        self,
        config: ProviderConfig,
    ) -> dict[str, Any] | None:
        policies = self._policies(config)
        if policies is None:
            return None
        return {role: rule.model_dump() for role, rule in policies.rate_limits.items()}

    async def get_session_state(
        self,
        session_id: str,
        config: ProviderConfig,
    ) -> dict[str, Any] | None:
        policies = self._policies(config)
        if policies is None:
            return None
        return {
            "session_id": session_id,
            **policies.session.model_dump(),
        }

    async def get_risk_assessment(
        self,
        request: dict[str, Any],
        config: ProviderConfig,
    ) -> dict[str, Any] | None:
        policies = self._policies(config)
        if policies is None:
            return None
        risk_policy = policies.risk_assessment
        for rule in risk_policy.rules:
            if all(request.get(k) == v for k, v in rule.match.items()):
                return {
                    "risk_score": rule.score,
                    "band": rule.band,
                    "rationale": f"matched_rule:{rule.match}",
                }
        return {
            "risk_score": risk_policy.default.score,
            "band": risk_policy.default.band,
            "rationale": "default_rule",
        }
