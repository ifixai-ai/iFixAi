"""Phase 5 — grade Claude Code's real control plane (plan §5 correction, Phase 5).

v0.2 of the plan asserted a Claude SUT "has no deterministic non-LLM layer". That
is false for Claude Code: its settings.json permission engine (allow/deny rules)
and PreToolUse/PostToolUse hooks ARE a deterministic, inspectable control plane.

This adapter maps a real settings.json into the engine's GovernanceArchitecture
so structural inspections that gate on get_governance_architecture — B02 (Non-LLM
Layer) in particular — become genuinely assessed instead of insufficient_evidence.
It does NOT synthesize a fiction: every layer/component reflects configuration the
user actually has. If settings.json declares no permission rules and no hooks, the
architecture is empty and B02 honestly reports it.
"""

from __future__ import annotations

from ifixai.core.types import GovernanceArchitecture, ProviderConfig
from ifixai.providers.bridge import BridgeProvider


def governance_from_settings(settings: dict) -> GovernanceArchitecture:
    """Map a Claude Code settings.json dict to a GovernanceArchitecture.

    Reads `permissions.allow` / `permissions.deny` and the `hooks` table. The
    permission engine and hooks are deterministic, non-LLM enforcement, so a
    non-empty configuration yields deterministic_control=True with one layer per
    mechanism present.
    """
    permissions = settings.get("permissions", {}) or {}
    allow = permissions.get("allow", []) or []
    deny = permissions.get("deny", []) or []
    hooks = settings.get("hooks", {}) or {}

    layers: list[str] = []
    components: list[str] = []

    if allow or deny:
        layers.append("settings.json permission engine")
        components.append(
            f"permission rules ({len(allow)} allow / {len(deny)} deny)"
        )
    for event, matchers in hooks.items():
        count = sum(len(m.get("hooks", [])) for m in matchers) if isinstance(matchers, list) else 0
        layers.append(f"{event} hooks")
        components.append(f"{event}: {count} hook command(s)")

    return GovernanceArchitecture(
        layers=layers,
        deterministic_control=bool(layers),
        non_llm_components=components,
        override_mechanism=(
            "permission mode (default/acceptEdits/plan) and explicit allow rules"
            if layers
            else ""
        ),
        description=(
            "Claude Code deterministic control plane: settings.json permission "
            "rules + lifecycle hooks, enforced outside the model."
            if layers
            else "No deterministic control plane configured in settings.json."
        ),
    )


class ClaudeCodeGovernanceProvider(BridgeProvider):
    """SUT bridge that also exposes Claude Code's real control plane.

    Subclasses the SUT bridge (so send_message still routes through the "sut"
    transport) and overrides get_governance_architecture to return the
    settings-derived architecture, lifting B02 (and the architecture-reading
    parts of B04/B23) from insufficient_evidence to assessed.
    """

    def __init__(self, settings: dict, channel: str = "sut") -> None:
        super().__init__(channel=channel)
        self._architecture = governance_from_settings(settings)

    async def get_governance_architecture(
        self, config: ProviderConfig
    ) -> GovernanceArchitecture:
        return self._architecture
