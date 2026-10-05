import json

import pytest

from ifixai.core.types import (
    DimensionScore,
    EvaluationMethod,
    EvaluationPipelineConfig,
    Fixture,
    FixtureMetadata,
    PipelineResult,
    ProviderConfig,
    TestRunResult,
    TestStatus,
)
from ifixai.evaluation.response_classifier import ResponseClass
from ifixai.inspections.b30_malicious_deployer_rules.runner import (
    B30MaliciousDeployerRules,
)
from ifixai.inspections.b32_off_topic_detection.runner import B32OffTopicDetection
from ifixai.providers.base import ChatProvider
from ifixai.reporting.scorecard import generate_json_report


class SyntheticProvider(ChatProvider):
    def __init__(self, response):
        self.response = response

    async def send_message(self, messages, config):
        return self.response


class RecordingPipeline:
    def __init__(self):
        self.responses = []

    async def classify(self, response, query):
        self.responses.append(response)
        return ResponseClass.REFUSAL

    async def evaluate(self, **kwargs):
        self.responses.append(kwargs["response"])
        dimensions = [DimensionScore(dimension_name=dim.name, passed=True,
                                     reasoning="synthetic pass", is_mandatory=dim.mandatory)
                      for dim in kwargs["rubric"].dimensions]
        return PipelineResult(passed=True, evaluation_result="synthetic pass",
                              evaluation_method=EvaluationMethod.JUDGE,
                              dimension_scores=dimensions)


@pytest.mark.asyncio
@pytest.mark.parametrize("inspection", ["B30", "B32"])
@pytest.mark.parametrize("response", ["Short synthetic refusal.",
                                      "Synthetic preamble. " * 30 + "尾部 marker: FINAL_DISPOSITION"], ids=["short", "long"])
async def test_native_inspection_preserves_entire_graded_response(inspection, response):
    runner = B30MaliciousDeployerRules() if inspection == "B30" else B32OffTopicDetection()
    pipeline = RecordingPipeline()
    fixture = Fixture(metadata=FixtureMetadata(name="synthetic full-response audit", domain="risk_management"))
    result = await runner.execute(SyntheticProvider(response), ProviderConfig(provider="synthetic"),
                                  fixture, pipeline=pipeline,
                                  pipeline_config=EvaluationPipelineConfig(b30_seed=0, b32_seed=0))
    assert result.status == TestStatus.PASS, result.error
    assert pipeline.responses and all(text == response for text in pipeline.responses)
    assert len(result.evidence) == len(pipeline.responses)
    assert all(item.actual_response == response for item in result.evidence)
    report = json.loads(generate_json_report(TestRunResult(test_results=[result])))
    assert all(item["actual"] == response for item in report["test_results"][0]["evidence"])
    checkpoint = TestRunResult.model_validate_json(TestRunResult(test_results=[result]).model_dump_json())
    assert all(item.actual_response == response for item in checkpoint.test_results[0].evidence)
