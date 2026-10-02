import asyncio

from ifixai.core.types import (
    AnalyticRubric,
    EvaluationCriteria,
    EvaluationPipelineConfig,
    RubricVerdict,
)
from ifixai.evaluation.pipeline import EvaluationPipeline


class SlowJudge:
    def __init__(self):
        self.calls = 0

    async def evaluate_with_rubric(self, response, rubric, context, context_vars):
        self.calls += 1
        await asyncio.sleep(0)
        return RubricVerdict(weighted_score=1.0, passed=True, verdict="pass")


def test_concurrent_evaluations_respect_judge_call_budget():
    judge = SlowJudge()
    pipeline = EvaluationPipeline(
        EvaluationPipelineConfig(judge_max_calls=1), judge=judge
    )
    rubric = AnalyticRubric(test_id="B01", outcome_type="comply", dimensions=[])

    async def run_both():
        return await asyncio.gather(
            pipeline.evaluate("first", EvaluationCriteria(), rubric),
            pipeline.evaluate("second", EvaluationCriteria(), rubric),
        )

    results = asyncio.run(run_both())

    assert judge.calls == 1
    assert pipeline.judge_calls_used == 1
    assert sum(result.passed for result in results) == 1
    assert sum("budget exhausted" in result.evaluation_result for result in results) == 1
