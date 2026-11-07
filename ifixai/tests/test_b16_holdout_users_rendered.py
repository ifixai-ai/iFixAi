"""B16's per-run holdout user ids must reach the prompts the agent sees."""

import asyncio
from pathlib import Path

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationMethod,
    EvaluationPipelineConfig,
    PipelineResult,
    ProviderConfig,
)
from ifixai.inspections.b16_silent_failure.runner import B16SilentFailure
from ifixai.providers.base import ChatProvider

_NONEXISTENT = "holdout-user-owned-7f3a"
_REVOKED = "holdout-revoked-owned-9c1d"


class _RecordingAgent(ChatProvider):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def send_message(self, messages, config):
        self.prompts.append(messages[-1].content)
        return "That request failed: the user is not recognised."


class _PassingJudge:
    async def evaluate(self, **_kwargs):
        return PipelineResult(passed=True, evaluation_result="ok",
                              evaluation_method=EvaluationMethod.JUDGE)


def test_holdout_users_appear_in_their_categories_prompts():
    agent = _RecordingAgent()
    config = ProviderConfig(provider="owned", holdout_ids={
        "b16_user_nonexistent": _NONEXISTENT, "b16_user_revoked": _REVOKED,
    })
    fixture = load_fixture(str(Path(__file__).resolve().parents[1] / "fixtures" / "smoke_tiny.yaml"))
    asyncio.run(B16SilentFailure().execute(
        agent, config, fixture, pipeline=_PassingJudge(),
        pipeline_config=EvaluationPipelineConfig(),
    ))

    assert any(_NONEXISTENT in prompt for prompt in agent.prompts)
    assert any(_REVOKED in prompt for prompt in agent.prompts)
    # Only the identity categories name a requesting user.
    with_user = [p for p in agent.prompts if "Requesting user:" in p]
    assert 0 < len(with_user) < len(agent.prompts)
    assert all(_NONEXISTENT in p or _REVOKED in p for p in with_user)
