import pytest
import yaml

from ifixai.core.types import (
    EvaluationMethod,
    EvaluationPipelineConfig,
    Fixture,
    FixtureMetadata,
    PipelineResult,
    ProviderConfig,
    TestStatus,
    User,
)
from ifixai.inspections.b12_prompt_injection.runner import B12PromptInjection
from ifixai.inspections.b14_covert_side_task.runner import B14CovertSideTask
from ifixai.providers.base import ChatProvider


class CountingProvider(ChatProvider):
    def __init__(self):
        self.calls = 0

    async def send_message(self, messages, config):
        self.calls += 1
        return "Synthetic corpus-loader boundary reply."


class PassingPipeline:
    async def evaluate(self, **kwargs):
        return PipelineResult(passed=True, evaluation_result="synthetic pass",
                              evaluation_method=EvaluationMethod.JUDGE)


def mount_corpus(kind, identifiers, tmp_path, monkeypatch):
    path = tmp_path / "corpus.yaml"
    if kind == "seed":
        corpus = {"seeds": [{"id": item, "text": f"Synthetic source {index}."}
                            for index, item in enumerate(identifiers)]}
        monkeypatch.setattr("ifixai.inspections.b14_covert_side_task.runner._CORPUS_PATH", path)
        runner = B14CovertSideTask()
    else:
        corpus = {"version": 1, "sample_per_category": 99,
                  "payloads": [{"id": item, "category": "synthetic",
                                "payload": f"Synthetic source {index}."}
                               for index, item in enumerate(identifiers)]}
        monkeypatch.setattr("ifixai.harness.injection_corpus._CORPUS_PATH", path)
        runner = B12PromptInjection()
    path.write_text(yaml.safe_dump(corpus), encoding="utf-8")
    return runner


async def execute(runner, provider):
    return await runner.execute(provider, ProviderConfig(provider="synthetic"),
                                Fixture(metadata=FixtureMetadata(name="synthetic corpus validation"),
                                        users=[User(user_id="u1", name="Operator", roles=["operator"])]),
                                pipeline=PassingPipeline(),
                                pipeline_config=EvaluationPipelineConfig(b12_seed=0, b14_seed=0))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["seed", "injection"])
@pytest.mark.parametrize("pair", [("alpha", " alpha "), (" alpha ", "alpha")])
async def test_native_runner_rejects_ids_that_collapse_after_trimming(kind, pair, tmp_path, monkeypatch):
    runner = mount_corpus(kind, [*pair, *[f"extra{index}" for index in range(10)]], tmp_path, monkeypatch)
    provider = CountingProvider()
    result = await execute(runner, provider)
    assert result.status == TestStatus.ERROR
    expected = "duplicate seed id" if kind == "seed" else "duplicate payload id"
    assert expected in result.error
    assert provider.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["seed", "injection"])
async def test_native_runner_keeps_distinct_padded_ids_valid(kind, tmp_path, monkeypatch):
    runner = mount_corpus(kind, [" alpha ", "beta", " gamma ", *[f"extra{index}" for index in range(10)]], tmp_path, monkeypatch)
    provider = CountingProvider()
    result = await execute(runner, provider)
    assert result.status == TestStatus.PASS
    assert provider.calls > 0
    assert len({item.test_case_id for item in result.evidence}) == len(result.evidence)
