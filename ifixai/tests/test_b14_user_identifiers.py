import json

import pytest

from ifixai.core.types import (
    EvaluationMethod,
    EvaluationPipelineConfig,
    Fixture,
    FixtureMetadata,
    PipelineResult,
    ProviderConfig,
    TestRunResult,
    TestStatus,
    User,
)
from ifixai.inspections.b14_covert_side_task.runner import B14CovertSideTask
from ifixai.providers.base import ChatProvider
from ifixai.reporting.scorecard import generate_json_report


class SyntheticProvider(ChatProvider):
    async def send_message(self, messages, config):
        return "Synthetic refusal for evidence identifier test."


class PassingPipeline:
    async def evaluate(self, **kwargs):
        return PipelineResult(passed=True, evaluation_result="synthetic pass",
                              evaluation_method=EvaluationMethod.JUDGE)


@pytest.mark.asyncio
async def test_native_b14_distinguishes_each_user_variant_in_export():
    fixture = Fixture(metadata=FixtureMetadata(name="synthetic B14 identifiers"),
                      users=[User(user_id="operator|seed=a", name="Operator", roles=["operator"]),
                             User(user_id="reviewer", name="Reviewer", roles=["reviewer"])])
    ids_by_run = []
    for _ in range(2):
        result = await B14CovertSideTask().execute(
            SyntheticProvider(), ProviderConfig(provider="synthetic"), fixture,
            pipeline_config=EvaluationPipelineConfig(b14_seed=0, b14_seed_pinned=True),
            pipeline=PassingPipeline())
        assert result.status == TestStatus.PASS
        assert len(result.evidence) > 2
        roles = [item.details["template_vars"]["role"] for item in result.evidence]
        assert roles.count("operator") == roles.count("reviewer") == len(roles) // 2
        exported = json.loads(generate_json_report(TestRunResult(test_results=[result])))
        ids = [item["test_case_id"] for item in exported["test_results"][0]["evidence"]]
        assert len(set(ids)) == len(result.evidence)
        ids_by_run.append(ids)
    assert ids_by_run[0] == ids_by_run[1]
