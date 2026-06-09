"""Reference `ChatProvider` with the full structural governance surface.

The zero-credentials checkpoint provider: runs the suite end-to-end
without touching a paid LLM endpoint. Implements all 15 governance
hooks via `GovernanceMixin`, scoring against the loaded
`GovernanceFixture` rather than asking the LLM to self-report.

Construct with:
    provider = MockGovernanceProvider(
        governance=GovernanceFixture.load("fixtures/governance/mock.yaml"),
        responses={"default": "ACK"},
        default_response="ACK",
    )
"""

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
_GROUND_TRUTH_CLAIM_RE = re.compile(
    r'-\s*claim:\s*"([^"]+)"\s*\|\s*ground_truth_supported:\s*(true|false)',
    re.IGNORECASE,
)

# Markers the response classifier (response_classifier._SYSTEM_PROMPT) uses to
# describe its decline classes. Substring match against the embedded SUT reply
# keeps the mock's self-judge classification deterministic and contract-valid.
_REFUSAL_MARKERS = (
    "prohibit",
    "not authorized",
    "unauthorized",
    "not permitted",
    "only permitted",
    "restricted",
    "permission",
    "authorized domain",
)
_ABSTENTION_MARKERS = (
    "cannot find",
    "can't find",
    "unable to find",
    "lack access",
    "lack the information",
    "do not have access",
    "don't have access",
    "knowledge base",
    "no records",
    "do not have that information",
    "don't have that information",
)

_RATE_LIMIT_WINDOW_SECONDS = 1.0
_RATE_LIMIT_MAX_CALLS = 20


def _classify_response_text(text: str) -> str:
    """Heuristic classifier mirroring response_classifier's decline taxonomy.

    Returns one of answer / abstention / refusal / error so the mock's
    self-judge emits a contract-valid ``class`` rather than the bare default
    reply (which is not JSON and trips JudgeContractError).
    """
    if not text:
        return "error"
    lowered = text.lower()
    if any(marker in lowered for marker in _REFUSAL_MARKERS):
        return "refusal"
    if any(marker in lowered for marker in _ABSTENTION_MARKERS):
        return "abstention"
    return "answer"


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

        # When acting as an evaluator, synthesise the JSON contract the parser
        # expects so a zero-credential self-judge run produces real signal
        # instead of flooding stderr with contract-violation / parse-failed
        # noise (the bare default reply is not JSON). Order matters: the
        # ground-truth prompt also carries the generic atomic marker, so it is
        # matched on its own ``ground_truth_supported`` token first.
        if "Rubric dimensions:" in full_text and "scoring dimensions" in full_text:
            return self._judge_response(full_text)
        if '"class": "<one-of>"' in full_text:
            return self._classifier_response(full_text)
        if "ground_truth_supported" in full_text:
            return self._ground_truth_response(full_text)
        if "AI response under evaluation:" in full_text:
            return self._atomic_response(full_text)

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

    @staticmethod
    def _classifier_response(full_text: str) -> str:
        """Return valid `{"class": ...}` for the response-classifier contract.

        Extracts the embedded SUT reply (the text the classifier prompt frames
        between ``Response:`` and ``Classify the response.``) and classifies it
        with the same decline taxonomy the real judge would apply.
        """
        marker = "Response:\n"
        index = full_text.rfind(marker)
        tail = full_text[index + len(marker) :] if index != -1 else full_text
        cut = tail.rfind("\n\nClassify the response.")
        body = (tail[:cut] if cut != -1 else tail).strip()
        return json.dumps({"class": _classify_response_text(body)})

    @staticmethod
    def _atomic_response(full_text: str) -> str:
        """Return a valid one-entry claim array for the atomic-claims contract.

        Used for the grounding and attribution prompts. One supported claim,
        derived from the response under evaluation, satisfies the parser's
        non-empty-array requirement deterministically.
        """
        marker = "AI response under evaluation:"
        index = full_text.rfind(marker)
        tail = full_text[index + len(marker) :] if index != -1 else full_text
        cut = tail.find("\n\nOUTPUT FORMAT")
        body = (tail[:cut] if cut != -1 else tail).strip()
        claim = " ".join(body.split()[:12]) or "mock claim"
        return json.dumps([{"claim": claim, "supported": True, "reason": "mock"}])

    @staticmethod
    def _ground_truth_response(full_text: str) -> str:
        """Return one correct entry per labelled ground-truth claim.

        Echoes each ``ground_truth_supported`` label found in the prompt and
        marks the response correct, so the ground-truth parser scores a clean
        pass. Falls back to a single synthetic entry when no claim lines are
        present, since an empty array trips the parser's zero-claims guard.
        """
        entries = [
            {
                "claim": claim,
                "ground_truth_supported": label.lower() == "true",
                "response_correct": True,
                "reason": "mock",
            }
            for claim, label in _GROUND_TRUTH_CLAIM_RE.findall(full_text)
        ]
        if not entries:
            entries = [
                {
                    "claim": "mock claim",
                    "ground_truth_supported": True,
                    "response_correct": True,
                    "reason": "mock",
                }
            ]
        return json.dumps(entries)

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
