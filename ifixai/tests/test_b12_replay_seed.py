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
)
from ifixai.harness.injection_corpus import load_injection_corpus, sample_corpus
from ifixai.inspections.b12_prompt_injection.runner import B12PromptInjection
from ifixai.providers.base import ChatProvider
from ifixai.reporting.scorecard import generate_markdown_report


class SyntheticProvider(ChatProvider):
    async def send_message(self, messages, config):
        return "Synthetic refusal used only to exercise the replay contract."


class PassingPipeline:
    async def evaluate(self, **kwargs):
        return PipelineResult(passed=True, evaluation_result="synthetic pass",
                              evaluation_method=EvaluationMethod.JUDGE)


@pytest.mark.asyncio
@pytest.mark.parametrize(("seed", "pinned"), [(0, True), (8675309, False)])
async def test_native_b12_preserves_effective_seed_and_pinning(seed, pinned):
    runner = B12PromptInjection()
    result = await runner.execute(
        SyntheticProvider(), ProviderConfig(provider="synthetic"),
        Fixture(metadata=FixtureMetadata(name="synthetic B12 replay test")),
        pipeline_config=EvaluationPipelineConfig(b12_seed=seed, b12_seed_pinned=pinned),
        pipeline=PassingPipeline())
    assert result.status == TestStatus.PASS
    expected = sample_corpus(load_injection_corpus(), seed=seed)
    assert [item.details["payload_id"] for item in result.evidence] == [item.id for item in expected]
    assert result.variant_seed == seed
    assert result.variant_seed_pinned is pinned
    label = "pinned" if pinned else "random"
    assert f"**B12** seed: {seed} ({label})" in generate_markdown_report(
        TestRunResult(test_results=[result]))


@pytest.mark.asyncio
async def test_native_b12_without_pipeline_config_records_drawn_seed(monkeypatch):
    monkeypatch.setattr("ifixai.inspections.b12_prompt_injection.runner.secrets.randbelow",
                        lambda upper: 123456)
    result = await B12PromptInjection().execute(
        SyntheticProvider(), ProviderConfig(provider="synthetic"),
        Fixture(metadata=FixtureMetadata(name="synthetic B12 random replay test")),
        pipeline=PassingPipeline())
    assert result.status == TestStatus.PASS
    expected = sample_corpus(load_injection_corpus(), seed=123456)
    assert [item.details["payload_id"] for item in result.evidence] == [item.id for item in expected]
    assert result.variant_seed == 123456
    assert result.variant_seed_pinned is False
