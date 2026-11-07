"""Two users sharing a user_id must not double-run one principal or collide evidence ids."""

import asyncio
from collections import Counter

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
from ifixai.inspections.user_selection import unique_by_user_id
from ifixai.providers.base import detect_capabilities

_RUNNERS = ["B15", "B17", "C02", "C05", "C11", "M12", "P13", "P19", "P22", "P27", "P32",
            "S02", "X04", "X11"]


class _PassingJudge:
    def is_ensemble_judge(self):
        return False

    def note_temperature_zero_required(self):
        pass

    def judge_temperature(self):
        return 0.0

    async def evaluate(self, response, criteria, rubric, references=None, context="",
                       context_vars=None, **_kwargs):
        dimensions = [
            DimensionScore(dimension_name=d.name, passed=True, reasoning="owned",
                           is_mandatory=d.mandatory)
            for d in (rubric.dimensions if rubric is not None else [])
        ]
        verdict = RubricVerdict(dimension_scores=dimensions, weighted_score=1.0,
                                passed=True, verdict="pass")
        return PipelineResult(passed=True, evaluation_result="pass",
                              evaluation_method=EvaluationMethod.JUDGE,
                              dimension_scores=dimensions, rubric_verdict=verdict)


def _fixture_with_repeated_user_id():
    fixture = _resolve_fixture("default")
    users = list(fixture.users)
    # A different principal written under the first user's id.
    repeat = users[1].model_copy(update={"user_id": users[0].user_id})
    return fixture.model_copy(update={"users": [users[0], repeat, *users[2:]]})


def test_unique_by_user_id_keeps_first_occurrence_in_file_order():
    users = _fixture_with_repeated_user_id().users
    unique = unique_by_user_id(users)
    assert [u.user_id for u in unique] == list(dict.fromkeys(u.user_id for u in users))
    assert unique[0] is users[0]


@pytest.mark.parametrize("test_id", _RUNNERS)
def test_repeated_user_id_does_not_collide_evidence_ids(test_id):
    fixture = _fixture_with_repeated_user_id()
    provider = _resolve_provider_with_governance("mock", fixture)
    config = _build_config("mock", "", None, None, None, 30, 0)

    async def run():
        return await create_inspection(test_id).execute(
            provider, config, fixture,
            capabilities=await detect_capabilities(provider, config),
            pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL),
            pipeline=_PassingJudge(),
        )

    result = asyncio.run(run())
    ids = Counter(item.test_case_id for item in result.evidence)
    assert len(ids) > 1
    assert [case for case, count in ids.items() if count > 1] == []
