import json

import pytest

from ifixai.api import run_selected
from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import TestStatus
from ifixai.judge.config import JudgeConfig
from ifixai.reporting.scorecard import generate_json_report


@pytest.mark.asyncio
@pytest.mark.parametrize("test_id", ["B18", "B19", "B20", "B21"])
async def test_json_retains_native_fixture_floor_reason(test_id):
    fixture = load_fixture("customer_support")
    fixture = fixture.model_copy(update={"users": fixture.users[:1]})
    result = await run_selected(
        {test_id}, "mock", fixture=fixture, judge_config=JudgeConfig(provider="mock"))
    inspection = result.test_results[0]
    assert inspection.status == TestStatus.INCONCLUSIVE
    assert inspection.error_message
    assert "fixture" in inspection.error_message
    assert not inspection.evidence
    exported = json.loads(generate_json_report(result))["test_results"][0]
    assert exported["score"] is None
    assert exported.get("error_message") == inspection.error_message


@pytest.mark.asyncio
async def test_json_still_preserves_native_error_reason():
    result = await run_selected({"B18"}, "mock", fixture="customer_support")
    inspection = result.test_results[0]
    assert inspection.status == TestStatus.ERROR
    assert inspection.error_message
    exported = json.loads(generate_json_report(result))["test_results"][0]
    assert exported["error_message"] == inspection.error_message
    assert exported["status"] == "error"
