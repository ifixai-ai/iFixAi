"""Every probe of these B-series inspections must carry its own evidence id."""

import asyncio
from collections import Counter
from pathlib import Path

import pytest

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationMethod,
    EvaluationPipelineConfig,
    PipelineResult,
    ProviderConfig,
)
from ifixai.harness.registry import create_inspection
from ifixai.providers.base import ChatProvider

_HEALTHCARE = Path(__file__).resolve().parents[1] / "fixtures" / "examples" / "healthcare.yaml"


class _Agent(ChatProvider):
    async def send_message(self, messages, config):
        return "I cannot do that; error: not found."


class _PassingJudge:
    async def evaluate(self, **_kwargs):
        return PipelineResult(passed=True, evaluation_result="ok",
                              evaluation_method=EvaluationMethod.JUDGE)


@pytest.mark.parametrize("test_id", ["B03", "B06", "B08", "B13", "B16", "B18", "B21", "B25"])
def test_every_probe_has_a_distinct_case_id(test_id):
    result = asyncio.run(create_inspection(test_id).execute(
        _Agent(), ProviderConfig(provider="owned"), load_fixture(str(_HEALTHCARE)),
        pipeline=_PassingJudge(), pipeline_config=EvaluationPipelineConfig(),
    ))
    ids = Counter(item.test_case_id for item in result.evidence)
    assert len(ids) > 1
    assert [case for case, count in ids.items() if count > 1] == []
