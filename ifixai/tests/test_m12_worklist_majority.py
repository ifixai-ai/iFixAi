"""M12's dependency worklist follows the judge majority, not one sample (as #406 did for P32/S02)."""

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
    PipelineResult,
    RubricVerdict,
)
from ifixai.harness.registry import create_inspection


class _SplitJudge:
    """Sample k of each arc fails the dimension `failing[k % 3]` (None passes every dimension)."""

    def __init__(self, failing: tuple[str | None, str | None, str | None]) -> None:
        self._failing = failing
        self._calls: dict[str, int] = {}

    def is_ensemble_judge(self):
        return False

    def note_temperature_zero_required(self):
        pass

    def judge_temperature(self):
        return 0.0

    async def evaluate(self, response, criteria, rubric, references=None, context="",
                       context_vars=None, **_kwargs):
        sample = self._calls.get(response, 0)
        self._calls[response] = sample + 1
        failing = self._failing[sample % 3]
        dimensions = [
            DimensionScore(dimension_name=d.name, reasoning="owned", is_mandatory=d.mandatory,
                           passed=d.name != failing)
            for d in rubric.dimensions
        ]
        passed = failing is None
        return PipelineResult(
            passed=passed, evaluation_result="pass" if passed else "fail",
            evaluation_method=EvaluationMethod.JUDGE,
            dimension_scores=dimensions,
            rubric_verdict=RubricVerdict(dimension_scores=dimensions, weighted_score=1.0 if passed else 0.0,
                                         passed=passed, verdict="pass" if passed else "fail",
                                         mandatory_veto=not passed),
        )


def _worklist(failing: tuple[str | None, str | None, str | None]):
    fixture = _resolve_fixture("default")
    provider = _resolve_provider_with_governance("mock", fixture)
    config = _build_config("mock", "", None, None, None, 30, 0)
    result = asyncio.run(create_inspection("M12").execute(
        provider, config, fixture, pipeline=_SplitJudge(failing),
        pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL),
    ))
    return next(e for e in result.evidence if e.test_case_id == "M12-dependency-summary").details


def test_the_worklist_names_the_dimension_the_majority_failed():
    details = _worklist(("honors_exit_request", "no_dependency_escalation", "no_dependency_escalation"))
    assert details["scored_count"] > 0
    assert details["flagged_count"] == details["scored_count"]
    assert {tuple(r["failed_dimensions"]) for r in details["flagged"]} == {("no_dependency_escalation",)}


def test_one_sample_failing_a_dimension_keeps_the_arc_off_the_worklist():
    details = _worklist(("honors_exit_request", None, None))
    assert details["scored_count"] > 0
    assert details["flagged_count"] == 0
    assert details["verdictless_failure_count"] == 0


def test_a_failed_arc_with_no_majority_dimension_is_still_counted():
    details = _worklist(
        ("honors_exit_request", "no_dependency_escalation", "no_alternative_discouragement")
    )
    assert details["scored_count"] > 0
    assert details["flagged_count"] == 0
    assert details["verdictless_failure_count"] == details["scored_count"]
