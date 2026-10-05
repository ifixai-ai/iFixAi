import pytest

from ifixai.core.types import (
    EvaluationPipelineConfig,
    Fixture,
    FixtureMetadata,
    ProviderConfig,
    TestRunResult,
    TestStatus,
)
from ifixai.evaluation.response_classifier import ResponseClass
from ifixai.harness.adversarial_mutator import expand_seeds, load_seeds
from ifixai.inspections.b30_malicious_deployer_rules import runner as b30
from ifixai.providers.base import ChatProvider
from ifixai.reporting.scorecard import generate_markdown_report


class SyntheticProvider(ChatProvider):
    async def send_message(self, messages, config):
        return "Synthetic B30 refusal for replay contract test."


class RefusalPipeline:
    async def classify(self, response, query):
        return ResponseClass.REFUSAL


@pytest.mark.asyncio
@pytest.mark.parametrize(("seed", "pinned"), [(0, True), (8675309, False)])
async def test_native_b30_records_actual_expansion_seed(seed, pinned):
    result = await b30.B30MaliciousDeployerRules().execute(
        SyntheticProvider(), ProviderConfig(provider="synthetic"),
        Fixture(metadata=FixtureMetadata(name="synthetic B30 replay")),
        pipeline=RefusalPipeline(),
        pipeline_config=EvaluationPipelineConfig(b30_seed=seed, b30_seed_pinned=pinned))
    assert result.status == TestStatus.PASS
    expected = expand_seeds(load_seeds(b30._CORPUS_PATH), b30._SYNONYMS_PATH,
                            seed=seed, variants_per_seed=b30._VARIANTS_PER_SEED)
    assert [item.prompt_sent for item in result.evidence] == [item.mutated_text for item in expected]
    assert result.variant_seed == seed
    assert result.variant_seed_pinned is pinned
    label = "pinned" if pinned else "random"
    assert f"**B30** seed: {seed} ({label})" in generate_markdown_report(
        TestRunResult(test_results=[result]))


@pytest.mark.asyncio
async def test_native_b30_unconfigured_seed_preserves_actual_draw(monkeypatch):
    monkeypatch.setattr("ifixai.inspections.b30_malicious_deployer_rules.runner.secrets.randbelow",
                        lambda upper: 123456)
    result = await b30.B30MaliciousDeployerRules().execute(
        SyntheticProvider(), ProviderConfig(provider="synthetic"),
        Fixture(metadata=FixtureMetadata(name="synthetic B30 random replay")),
        pipeline=RefusalPipeline())
    assert result.status == TestStatus.PASS
    expected = expand_seeds(load_seeds(b30._CORPUS_PATH), b30._SYNONYMS_PATH,
                            seed=123456, variants_per_seed=b30._VARIANTS_PER_SEED)
    assert [item.prompt_sent for item in result.evidence] == [item.mutated_text for item in expected]
    assert result.variant_seed == 123456
    assert result.variant_seed_pinned is False
