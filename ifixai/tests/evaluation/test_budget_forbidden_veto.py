import asyncio

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationCriteria,
    EvaluationPipelineConfig,
    ProviderConfig,
)
from ifixai.evaluation.analytic_judge import load_analytic_rubric, parse_rubric_verdict
from ifixai.evaluation.pipeline import EvaluationPipeline
from ifixai.inspections.b06_uncertainty_signalling.runner import (
    B06UncertaintySignalling,
)
from ifixai.providers.base import ChatProvider


class ValidJudge:
    def __init__(self):
        self.calls = 0

    async def evaluate_with_rubric(self, response, rubric, context, context_vars):
        import json
        self.calls += 1
        return parse_rubric_verdict(json.dumps({'dimensions': [
            {'name': d.name, 'passed': True} for d in rubric.dimensions
        ]}), rubric)


class OverconfidentSUT(ChatProvider):
    async def send_message(self, messages, config):
        return 'The result will definitely double without doubt.'


def test_shipped_b06_veto_survives_exhausted_judge_budget():
    async def run():
        judge = ValidJudge()
        pipeline = EvaluationPipeline(EvaluationPipelineConfig(judge_max_calls=1), judge)
        rubric = await load_analytic_rubric('B06', 'comply')
        await pipeline.evaluate('The forecast is uncertain.', EvaluationCriteria(), rubric)
        fixture = load_fixture('customer_support')
        inspection = B06UncertaintySignalling()
        result = await inspection.execute(
            OverconfidentSUT(), ProviderConfig(provider='custom'), fixture,
            pipeline_config=EvaluationPipelineConfig(judge_max_calls=1), pipeline=pipeline,
        )
        assert result.evidence, result.error_message
        assert all(e.rubric_verdict is not None and e.rubric_verdict.mandatory_veto for e in result.evidence)
        assert result.score == 0.0
        assert judge.calls == pipeline.judge_calls_used == 1
    asyncio.run(run())


def test_exhausted_budget_still_prevents_non_veto_judge_calls():
    async def run():
        judge = ValidJudge()
        pipeline = EvaluationPipeline(EvaluationPipelineConfig(judge_max_calls=1), judge)
        rubric = await load_analytic_rubric('B06', 'comply')
        criteria = EvaluationCriteria(deterministic_forbidden_veto=True, forbidden_keywords=['will definitely'])
        first = await pipeline.evaluate('The forecast is uncertain.', criteria, rubric)
        second = await pipeline.evaluate('The forecast remains uncertain.', criteria, rubric)
        assert first.passed
        assert not second.passed and 'budget exhausted' in second.evaluation_result
        assert second.rubric_verdict is None
        assert judge.calls == 1
    asyncio.run(run())
