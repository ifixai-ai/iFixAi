"""B08 and B17 score a subset of their evidence; their CI must cover the same subset."""

import asyncio
from pathlib import Path

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationMethod,
    EvaluationMode,
    EvaluationPipelineConfig,
    PipelineResult,
    ProviderCapabilities,
    ProviderConfig,
    ToolInvocationResult,
)
from ifixai.inspections.b08_privilege_escalation.runner import B08PrivilegeEscalation
from ifixai.inspections.b17_fact_consistency.runner import B17FactConsistency
from ifixai.providers.base import ChatProvider

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class _Agent(ChatProvider):
    def __init__(self, reply: str, authorize: bool = False) -> None:
        self._reply = reply
        self._authorize = authorize

    async def send_message(self, messages, config):
        return self._reply

    async def invoke_tool(self, tool_id, user_role, params, config):
        # Structural bonus items: the API lets the escalation through.
        return ToolInvocationResult(authorized=self._authorize, executed=self._authorize)

    async def retrieve_sources(self, query, config):
        return []  # a retrieval layer that finds nothing fails the structural stream


class _PassingJudge:
    async def evaluate(self, **_kwargs):
        return PipelineResult(passed=True, evaluation_result="ok",
                              evaluation_method=EvaluationMethod.JUDGE)


def _execute(inspection, fixture, provider, capabilities):
    return asyncio.run(inspection.execute(
        provider, ProviderConfig(provider="owned"), load_fixture(str(fixture)),
        capabilities=capabilities, pipeline=_PassingJudge(),
        pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL),
    ))


def _assert_ci_describes_score(inspection, result):
    scored = inspection.ci_evidence(result.evidence)
    assert 0 < len(scored) < len(result.evidence)
    ci = result.confidence_interval
    assert ci.sample_size == len(scored)
    assert ci.lower <= result.score <= ci.upper


def test_b08_ci_excludes_structural_bonus_items():
    inspection = B08PrivilegeEscalation()
    result = _execute(inspection, _FIXTURES / "smoke_tiny.yaml",
                      _Agent("No, I can't grant that.", authorize=True),
                      ProviderCapabilities(has_tool_calling=True))
    assert result.score == 1.0
    _assert_ci_describes_score(inspection, result)


def test_b17_ci_excludes_structural_retrieval_items():
    inspection = B17FactConsistency()
    result = _execute(inspection, _FIXTURES / "examples" / "healthcare.yaml",
                      _Agent("Same answer as before."),
                      ProviderCapabilities(has_retrieval=True))
    assert result.score == 1.0
    _assert_ci_describes_score(inspection, result)
