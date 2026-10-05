import asyncio

import pytest

from ifixai.evaluation import analytic_judge


@pytest.fixture(autouse=True)
def fresh_rubric_cache(monkeypatch):
    monkeypatch.setattr(analytic_judge, '_rubric_cache', {})
    # Start without prior loader calls; all contention comes from actual YAML IO.
    if hasattr(analytic_judge, '_rubric_cache_lock'):
        monkeypatch.setattr(analytic_judge, '_rubric_cache_lock', None)


def load_pair(first, second):
    async def load():
        return await asyncio.gather(
            analytic_judge.load_analytic_rubric(first, 'comply'),
            analytic_judge.load_analytic_rubric(second, 'comply'),
        )
    return asyncio.run(load())


def test_concurrent_rubric_loads_work_across_successive_api_event_loops():
    first = load_pair('B06', 'B07')
    second = load_pair('B08', 'B09')
    assert [r.test_id for r in first] == ['B06', 'B07']
    assert [r.test_id for r in second] == ['B08', 'B09']


def test_same_loop_repeated_loads_share_cached_rubric():
    results = load_pair('B06', 'B06')
    assert results[0] is results[1]
    assert results[0].references is not None


def test_public_parallel_runs_load_new_rubrics_on_a_second_event_loop():
    from ifixai.api import run_selected
    from ifixai.core.concurrency import ConcurrencyGovernor
    from ifixai.core.types import EvaluationPipelineConfig, TestStatus
    from ifixai.judge.config import JudgeConfig

    async def run(ids):
        return await run_selected(
            ids, provider='mock', fixture='customer_support',
            pipeline_config=EvaluationPipelineConfig(judge_max_calls=2000),
            judge_config=JudgeConfig(provider='mock'), governor=ConcurrencyGovernor(2),
        )
    for ids in [{'B06', 'B08'}, {'B09', 'B10'}]:
        result = asyncio.run(run(ids))
        assert all(r.status is not TestStatus.ERROR for r in result.test_results), [
            r.error_message for r in result.test_results
        ]
