"""P13's coverage diagnostic must not pad its evidence floor."""

import asyncio

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
    JudgeErrorKind,
    PipelineResult,
    RubricVerdict,
    TestStatus,
)
from ifixai.inspections.p13_hidden_side_objective_persistence.runner import (
    P13HiddenSideObjectivePersistence,
)

_FLOOR = P13HiddenSideObjectivePersistence().spec.min_evidence_items


class _Judge:
    """Grades the first `graded` distinct trajectories as passing; errors on the rest."""

    def __init__(self, graded: int) -> None:
        self._graded = graded
        self._seen: list[str] = []

    def is_ensemble_judge(self):
        return False

    def note_temperature_zero_required(self):
        pass

    def judge_temperature(self):
        return 0.0

    async def evaluate(self, response, criteria, rubric, references=None, context="",
                       context_vars=None, **_kwargs):
        if response not in self._seen and len(self._seen) < self._graded:
            self._seen.append(response)
        if response not in self._seen:
            return PipelineResult(passed=False, evaluation_result="extraction_error: owned",
                                  evaluation_method=EvaluationMethod.JUDGE,
                                  extraction_error=JudgeErrorKind.EXTRACTION)
        dimensions = [DimensionScore(dimension_name=d.name, passed=True, reasoning="owned",
                                     is_mandatory=d.mandatory) for d in rubric.dimensions]
        return PipelineResult(
            passed=True, evaluation_result="pass", evaluation_method=EvaluationMethod.JUDGE,
            dimension_scores=dimensions,
            rubric_verdict=RubricVerdict(dimension_scores=dimensions, weighted_score=1.0,
                                         passed=True, verdict="pass"),
        )


def _run(graded: int):
    fixture = _resolve_fixture("default")
    provider = _resolve_provider_with_governance("mock", fixture)
    config = _build_config("mock", "", None, None, None, 30, 0)
    inspection = P13HiddenSideObjectivePersistence()
    result = asyncio.run(inspection.execute(
        provider, config, fixture, pipeline=_Judge(graded),
        pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL),
    ))
    scored = [e for e in result.evidence
              if e.test_case_id.startswith("P13-traj-") and e.extraction_error is None]
    return inspection, result, scored


def test_below_floor_after_judge_errors_is_inconclusive():
    inspection, result, scored = _run(graded=_FLOOR - 1)
    assert len(scored) < inspection.spec.min_evidence_items
    assert result.status is TestStatus.INCONCLUSIVE
    assert result.insufficient_evidence is True


def test_at_floor_still_grades():
    inspection, result, scored = _run(graded=_FLOOR)
    assert len(scored) == inspection.spec.min_evidence_items
    assert result.status is TestStatus.PASS
