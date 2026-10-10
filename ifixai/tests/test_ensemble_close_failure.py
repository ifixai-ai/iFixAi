import pytest

from ifixai.judge.config import JudgeConfig, JudgeProviderSpec
from ifixai.judge.evaluator import EnsembleJudgeEvaluator


@pytest.mark.asyncio
async def test_all_ensemble_providers_close_after_one_failure(monkeypatch):
    ensemble = EnsembleJudgeEvaluator(
        JudgeConfig(
            providers=[
                JudgeProviderSpec(provider="mock"),
                JudgeProviderSpec(provider="mock"),
            ]
        )
    )
    closed = []
    failure = RuntimeError("close failed")

    async def first_close():
        closed.append("first")
        raise failure

    async def second_close():
        closed.append("second")

    monkeypatch.setattr(ensemble.evaluators[0]._provider, "aclose", first_close)
    monkeypatch.setattr(ensemble.evaluators[1]._provider, "aclose", second_close)
    with pytest.raises(RuntimeError) as caught:
        await ensemble.aclose()
    assert caught.value is failure
    assert set(closed) == {"first", "second"}
