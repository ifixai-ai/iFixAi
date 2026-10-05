import json

import pytest

from ifixai.core.types import (
    EvaluationMethod,
    EvaluationPipelineConfig,
    EvidenceItem,
    Fixture,
    FixtureMetadata,
    JudgeErrorKind,
    PipelineResult,
    ProviderConfig,
    TestResult,
    TestRunResult,
    TestStatus,
    User,
)
from ifixai.inspections.b14_covert_side_task.runner import B14CovertSideTask
from ifixai.providers.base import ChatProvider, ProviderConnectionError
from ifixai.reporting.scorecard import generate_json_report, generate_markdown_report
from ifixai.shared.evidence import flag_diagnostics


class SyntheticProvider(ChatProvider):
    def __init__(self):
        self.calls = 0

    async def send_message(self, messages, config):
        self.calls += 1
        if self.calls == 1:
            raise ProviderConnectionError(details="synthetic transport interruption")
        return "Synthetic refusal for export contract testing."


class PassingPipeline:
    async def evaluate(self, **kwargs):
        return PipelineResult(passed=True, evaluation_result="synthetic pass",
                              evaluation_method=EvaluationMethod.JUDGE)


@pytest.mark.asyncio
@pytest.mark.parametrize("projection", ["replay", "evidence"])
async def test_json_preserves_native_b14_audit_metadata(projection):
    fixture = Fixture(metadata=FixtureMetadata(name="synthetic audit metadata"),
                      users=[User(user_id="u1", name="Operator", roles=["operator"])])
    result = await B14CovertSideTask().execute(
        SyntheticProvider(), ProviderConfig(provider="synthetic"), fixture,
        pipeline_config=EvaluationPipelineConfig(b14_seed=0, b14_seed_pinned=True),
        pipeline=PassingPipeline())
    assert result.status == TestStatus.PASS
    run = TestRunResult(test_results=[result])
    exported = json.loads(generate_json_report(run))["test_results"][0]
    if projection == "replay":
        assert "**B14** seed: 0 (pinned)" in generate_markdown_report(run)
        assert exported["variant_seed"] == result.variant_seed == 0
        assert exported["variant_seed_pinned"] is True
        assert exported["score_breakdown"] == result.score_breakdown
    else:
        failed = result.evidence[0]
        assert failed.extraction_error == JudgeErrorKind.COMMUNICATION
        assert exported["evidence"][0]["extraction_error"] == "communication"
        for original, item in zip(result.evidence, exported["evidence"], strict=True):
            assert item["details"] == original.model_dump(mode="json")["details"]
            assert item.get("is_diagnostic", False) == original.is_diagnostic
            assert item.get("extraction_error") == (
                original.extraction_error.value if original.extraction_error else None)


def test_json_preserves_diagnostic_flag_and_structured_details():
    evidence = flag_diagnostics([
        EvidenceItem(test_case_id="coverage-summary", passed=False,
                     details={"scored": 3, "bands": ["public", "private"]}),
        EvidenceItem(test_case_id="measurement", passed=True),
    ], ("coverage-",))
    run = TestRunResult(test_results=[TestResult(test_id="B14", evidence=evidence)])
    exported = json.loads(generate_json_report(run))["test_results"][0]["evidence"]
    assert exported[0]["is_diagnostic"] is True
    assert exported[0]["details"] == evidence[0].details
    assert exported[1].get("is_diagnostic", False) is False
    assert exported[1].get("extraction_error") is None
