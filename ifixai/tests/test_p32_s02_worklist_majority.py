"""P32's anomaly and S02's over-flag worklists follow the judge majority, not one sample."""

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

_SOFT = {"P32": "undeclared_systemic_anomaly_absent", "S02": "no_fabricated_conflict"}
_WORKLIST = {"P32": "P32-anomaly-summary", "S02": "S02-overflag-summary"}


class _SplitJudge:
    """Every sample passes overall; the samples in `failing` fail the soft dimension."""

    def __init__(self, soft: str, failing: set[int]) -> None:
        self._soft = soft
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
        fails_soft = sample % 3 in self._failing
        dimensions = [
            DimensionScore(dimension_name=d.name, reasoning="owned", is_mandatory=d.mandatory,
                           passed=not (fails_soft and d.name == self._soft))
            for d in rubric.dimensions
        ]
        return PipelineResult(
            passed=True, evaluation_result="pass", evaluation_method=EvaluationMethod.JUDGE,
            dimension_scores=dimensions,
            rubric_verdict=RubricVerdict(dimension_scores=dimensions, weighted_score=0.9,
                                         passed=True, verdict="pass"),
        )


def _worklist(test_id: str, failing: set[int]):
    fixture = _resolve_fixture("default")
    provider = _resolve_provider_with_governance("mock", fixture)
    config = _build_config("mock", "", None, None, None, 30, 0)
    result = asyncio.run(create_inspection(test_id).execute(
        provider, config, fixture, pipeline=_SplitJudge(_SOFT[test_id], failing),
        pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL),
    ))
    worklist = next(e for e in result.evidence if e.test_case_id == _WORKLIST[test_id])
    return worklist.details["flagged_count"], worklist.details["scored_count"]


@pytest.mark.parametrize("test_id", ["P32", "S02"])
def test_majority_flag_reaches_the_worklist(test_id):
    flagged, scored = _worklist(test_id, failing={1, 2})  # 2 of 3 samples flag it
    assert scored > 0
    assert flagged == scored


@pytest.mark.parametrize("test_id", ["P32", "S02"])
def test_minority_flag_stays_off_the_worklist(test_id):
    flagged, scored = _worklist(test_id, failing={0})  # only 1 of 3 samples flags it
    assert scored > 0
    assert flagged == 0
