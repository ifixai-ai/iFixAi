import json

import pytest

from ifixai.core.types import (
    DataSource,
    EvaluationMethod,
    Fixture,
    FixtureMetadata,
    PipelineResult,
    ProviderConfig,
    TestRunResult,
    TestStatus,
    Tool,
    User,
)
from ifixai.inspections.b24_risk_scoring.runner import B24RiskScoring
from ifixai.providers.base import ChatProvider
from ifixai.reporting.scorecard import generate_json_report


class RecordingProvider(ChatProvider):
    def __init__(self):
        self.prompts = []

    async def send_message(self, messages, config):
        self.prompts.append(messages[-1].content)
        return "Synthetic risk assessment for identifier contract test."


class PassingPipeline:
    async def evaluate(self, **kwargs):
        return PipelineResult(passed=True, evaluation_result="synthetic pass",
                              evaluation_method=EvaluationMethod.JUDGE)


def fixture(tool_count):
    return Fixture(
        metadata=FixtureMetadata(name="synthetic identifier test"),
        users=[User(user_id="u1", name="Operator", roles=["operator"])],
        tools=[Tool(tool_id=f"tool{index}", name=f"Tool {index}")
               for index in range(tool_count)],
        data_sources=[
            DataSource(source_id="public", name="Public records", classification="public"),
            DataSource(source_id="private", name="Private records", classification="private"),
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_count", [2, 12])
async def test_b24_native_runner_exports_unique_repeatable_probe_ids(tool_count):
    config = ProviderConfig(provider="synthetic")
    reports = []
    for _ in range(2):
        provider = RecordingProvider()
        result = await B24RiskScoring().execute(
            provider, config, fixture(tool_count), pipeline=PassingPipeline())
        assert result.status == TestStatus.PASS
        expected_count = min(tool_count * 8, 40)
        assert len(result.evidence) == len(provider.prompts) == expected_count
        assert len(set(provider.prompts)) == expected_count
        report = json.loads(generate_json_report(TestRunResult(test_results=[result])))
        evidence = report["test_results"][0]["evidence"]
        ids = [item["test_case_id"] for item in evidence]
        assert len(set(ids)) == expected_count
        assert [item["prompt_sent"] for item in evidence] == provider.prompts
        reports.append(ids)
    assert reports[0] == reports[1]


@pytest.mark.asyncio
async def test_b24_probe_ids_keep_fixture_component_boundaries():
    synthetic = fixture(2).model_copy(update={
        "users": [
            User(user_id="u1", name="First", roles=["operator_tool"]),
            User(user_id="u2", name="Second", roles=["operator"]),
        ],
        "tools": [Tool(tool_id="a", name="First tool"),
                  Tool(tool_id="tool_a", name="Second tool")],
    })
    provider = RecordingProvider()
    result = await B24RiskScoring().execute(
        provider, ProviderConfig(provider="synthetic"), synthetic,
        pipeline=PassingPipeline())
    assert result.status == TestStatus.PASS
    assert len(result.evidence) == 32
    assert len({item.test_case_id for item in result.evidence}) == 32
    assert len(set(provider.prompts)) == 32
