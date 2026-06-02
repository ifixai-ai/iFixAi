"""Reference `ChatProvider` with the full structural governance surface.

The zero-credentials checkpoint provider: runs the suite end-to-end
without touching a paid LLM endpoint. Implements all 13 governance
hooks via `GovernanceMixin`, scoring against the loaded
`GovernanceFixture` rather than asking the LLM to self-report.

Construct with:
    provider = MockGovernanceProvider(
        governance=GovernanceFixture.load("fixtures/governance/mock.yaml"),
        responses={"default": "ACK"},
        default_response="ACK",
    )
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from typing import Optional

from ifixai.providers.base import (
    ChatProvider,
    ProviderCapability,
    ProviderRateLimitError,
)
from ifixai.providers.governance_fixture import GovernanceFixture
from ifixai.providers.governance_mixin import GovernanceMixin
from ifixai.core.types import ChatMessage, ProviderConfig

_DIM_NAME_RE = re.compile(r"^\s*-\s+(\S+?)(?:\s+\[MANDATORY\])?:", re.MULTILINE)

_RATE_LIMIT_WINDOW_SECONDS = 1.0
_RATE_LIMIT_MAX_CALLS = 20


class MockGovernanceProvider(GovernanceMixin, ChatProvider):
    """In-memory provider with a full structural governance surface."""

    def __init__(
        self,
        governance: GovernanceFixture,
        responses: Optional[dict[str, str]] = None,
        default_response: str = "ACK",
    ) -> None:
        self._governance = governance
        self._responses = responses or {}
        self._default_response = default_response
        self._call_timestamps: deque[float] = deque()

    async def send_message(
        self,
        messages: list[ChatMessage],
        config: ProviderConfig,
    ) -> str:
        self._enforce_rate_limit(config)

        full_text = "\n".join(m.content for m in messages)

        # When acting as the analytic judge, synthesise a valid contract response
        # so callers do not crash on a missing 'dimensions' key.
        if "Rubric dimensions:" in full_text and "scoring dimensions" in full_text:
            return self._judge_response(full_text)

        # Deterministic: first matching keyword wins, else default_response.
        # Keeps replies stable across runs so reproducibility tests are
        # meaningful. Governance-declared replies (set when a fixture's
        # `governance:` block carries `responses` / `default_response`) model
        # the SUT's deployed conversational behaviour; constructor-supplied
        # `_responses` override them for programmatic construction.
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        gov_responses = self._governance.responses if self._governance else {}
        for keyword, reply in {**gov_responses, **self._responses}.items():
            if keyword in last_user:
                return reply
        gov_default = self._governance.default_response if self._governance else None
        if gov_default is not None:
            return gov_default
        return self._default_response

    @staticmethod
    def _judge_response(full_text: str) -> str:
        """Return a minimal valid judge JSON for the rubric dimensions in the prompt."""
        names = _DIM_NAME_RE.findall(full_text)
        dims = [
            {"name": name, "passed": True, "reasoning": "mock pass"} for name in names
        ]
        return json.dumps({"dimensions": dims, "overall_reasoning": "mock evaluation"})

    def _enforce_rate_limit(self, config: ProviderConfig) -> None:
        if not config.soak_probes:
            return
        now = time.monotonic()
        while (
            self._call_timestamps
            and now - self._call_timestamps[0] > _RATE_LIMIT_WINDOW_SECONDS
        ):
            self._call_timestamps.popleft()
        self._call_timestamps.append(now)
        if len(self._call_timestamps) > _RATE_LIMIT_MAX_CALLS:
            raise ProviderRateLimitError(
                f"Mock rate limit exceeded: >{_RATE_LIMIT_MAX_CALLS} calls/sec"
            )

    @classmethod
    def capabilities(cls) -> frozenset[ProviderCapability]:
        return frozenset(ProviderCapability)
