"""P13/P19/P22/P27/P32/S02 score a subset; their CI must cover that subset only."""

import asyncio

import pytest

from ifixai.api import (
    _build_config,
    _resolve_fixture,
    _resolve_provider_with_governance,
)
from ifixai.core.types import (
    DimensionScore,
    EvaluationMethod,
    EvaluationMode,
    EvaluationPipelineConfig,
    PipelineResult,
    RubricVerdict,
)
from ifixai.harness.registry import create_inspection
from ifixai.providers.base import detect_capabilities


class _FailingJudge:
    """Every probe fails: the agent did the forbidden thing everywhere."""

    def is_ensemble_judge(self):
        return False

    def note_temperature_zero_required(self):
        pass

    def judge_temperature(self):
        return 0.0

    async def evaluate(self, response, criteria, rubric, references=None, context="",
                       context_vars=None, **_kwargs):
        dimensions = [
            DimensionScore(dimension_name=d.name, passed=False, reasoning="owned",
                           is_mandatory=d.mandatory)
            for d in (rubric.dimensions if rubric is not None else [])
        ]
        verdict = RubricVerdict(dimension_scores=dimensions, weighted_score=0.0,
                                passed=False, verdict="fail")
        return PipelineResult(passed=False, evaluation_result="fail",
                              evaluation_method=EvaluationMethod.JUDGE,
                              dimension_scores=dimensions, rubric_verdict=verdict)


async def _run(test_id):
    fixture = _resolve_fixture("default")
    provider = _resolve_provider_with_governance("mock", fixture)
    config = _build_config("mock", "", None, None, None, 30, 0)
    inspection = create_inspection(test_id)
    result = await inspection.execute(
        provider, config, fixture,
        capabilities=await detect_capabilities(provider, config),
        pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL),
        pipeline=_FailingJudge(),
    )
    return inspection, result


@pytest.mark.parametrize("test_id", ["P13", "P19", "P22", "P27", "P32", "S02"])
def test_ci_is_computed_over_the_scored_population(test_id):
    inspection, result = asyncio.run(_run(test_id))
    scored = inspection.ci_evidence(result.evidence)
    assert 0 < len(scored) < len(result.evidence)
    assert result.score == 0.0
    ci = result.confidence_interval
    assert ci.sample_size == len(scored)
    assert ci.lower <= result.score <= ci.upper
