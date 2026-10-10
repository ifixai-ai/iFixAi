"""Overlapping local API experiments must keep their inspection state separate."""

import asyncio

import pytest

from ifixai import api
from ifixai.core.concurrency import ConcurrencyGovernor
from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import EvaluationMode, EvaluationPipelineConfig
from ifixai.judge.config import JudgeConfig
from ifixai.providers.base import ChatProvider


class OwnedSut(ChatProvider):
    def __init__(self, entered, release):
        self.entered = entered
        self.release = release
        self.prompts = []

    async def send_message(self, messages, config):
        self.prompts.append(messages[-1].content)
        if len(self.prompts) == 1:
            self.entered.set()
            await self.release.wait()
        return "The data window is limited; any forecast is tentative."


def fixture_for(owner):
    fixture = load_fixture("software_engineering").model_copy(deep=True)
    fixture.data_sources = [fixture.data_sources[0].model_copy(update={"name": f"owned-store-{owner}"})]
    return fixture


async def invoke(entry, provider, fixture, mode, budget=0):
    kwargs = dict(
        provider=provider, fixture=fixture,
        pipeline_config=EvaluationPipelineConfig(mode=mode, judge_max_calls=budget),
        judge_config=JudgeConfig(provider="mock"),
    )
    if entry == "single":
        return await api.run_single("B06", **kwargs)
    result = await api.run_selected(
        {"B06"}, **kwargs,
        governor=ConcurrencyGovernor(2) if entry == "parallel" else None,
    )
    return result.test_results[0]


@pytest.mark.parametrize("entry", ["single", "selected", "parallel"])
async def test_overlapping_native_api_experiments_keep_fixture_and_mode(entry):
    entered_a, release_a, entered_b, release_b = [asyncio.Event() for _ in range(4)]
    owner_a, owner_b = OwnedSut(entered_a, release_a), OwnedSut(entered_b, release_b)
    tasks = []
    try:
        tasks.append(asyncio.create_task(invoke(entry, owner_a, fixture_for("A"), EvaluationMode.FULL)))
        await asyncio.wait_for(entered_a.wait(), timeout=5)
        tasks.append(asyncio.create_task(invoke(entry, owner_b, fixture_for("B"), EvaluationMode.SINGLE, budget=1)))
        await asyncio.wait_for(entered_b.wait(), timeout=5)
        release_a.set()
        result_a = await asyncio.wait_for(tasks[0], timeout=10)
        release_b.set()
        result_b = await asyncio.wait_for(tasks[1], timeout=10)
    finally:
        release_a.set()
        release_b.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert owner_a.prompts and owner_b.prompts
    assert all("owned-store-A" in prompt and "owned-store-B" not in prompt for prompt in owner_a.prompts)
    assert all("owned-store-B" in prompt and "owned-store-A" not in prompt for prompt in owner_b.prompts)
    assert all(item.extraction_error is None for item in result_a.evidence)
    assert sum(item.extraction_error is None for item in result_b.evidence) == 1
    assert result_a.evaluation_mode == EvaluationMode.FULL
    assert result_a.confidence_interval is not None
    assert result_b.evaluation_mode == EvaluationMode.SINGLE
    assert result_b.confidence_interval is None


@pytest.mark.parametrize("mode", [EvaluationMode.FULL, EvaluationMode.SINGLE])
async def test_sequential_experiment_control(mode):
    entered, release = asyncio.Event(), asyncio.Event()
    release.set()
    provider = OwnedSut(entered, release)
    result = await invoke("single", provider, fixture_for("control"), mode)
    assert all("owned-store-control" in prompt for prompt in provider.prompts)
    assert result.evaluation_mode == mode
    assert (result.confidence_interval is not None) == (mode == EvaluationMode.FULL)
