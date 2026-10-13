"""Completed empty replies retain each registered runner's denominator."""
import json
import re
from collections import Counter
from contextlib import asynccontextmanager

import aiohttp.web
import pytest

from ifixai.api import run_single
from ifixai.core.types import (
    ChatMessage,
    EvaluationMode,
    EvaluationPipelineConfig,
    ProviderConfig,
    TestStatus,
)
from ifixai.judge.config import JudgeConfig
from ifixai.providers.base import ProviderResponseError
from ifixai.providers.http import HttpProvider
from ifixai.tests.providers.test_http_empty_content_native import owned_endpoint


@asynccontextmanager
async def runner_endpoint(bad_content, empty_count=1, all_empty=False):
    calls = Counter()

    async def respond(request):
        payload = await request.json()
        model = payload.get("model", "retrieve")
        calls[model] += 1
        if request.path.endswith("retrieve"):
            return aiohttp.web.json_response({"sources": []})
        content = "The request failed: the resource does not exist. Please seek manual review."
        if model == "judge":
            prompt = "\n".join(item["content"] for item in payload["messages"])
            names = re.findall(r"^  - ([\w]+)(?: \[MANDATORY\])?:", prompt, re.MULTILINE)
            assert names, prompt
            content = json.dumps({"dimensions": [
                {"name": name, "passed": True, "reasoning": "synthetic native owner"}
                for name in names
            ]})
        elif all_empty or 2 <= calls[model] < 2 + empty_count:
            content = bad_content
        return aiohttp.web.json_response({"choices": [{
            "message": {"role": "assistant", "content": content}, "finish_reason": "stop",
        }]})

    app = aiohttp.web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    app.router.add_post("/v1/retrieve", respond)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1", calls
    finally:
        await runner.cleanup()


async def execute_registered(test_id, content, empty_count=1, all_empty=False):
    async with runner_endpoint(content, empty_count, all_empty) as (endpoint, calls):
        result = await run_single(
            test_id, provider=HttpProvider(), fixture="default", endpoint=endpoint,
            model="sut", timeout=5, max_retries=0,
            judge_config=JudgeConfig(provider="http", endpoint=endpoint, model="judge"),
            pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL),
        )
        return result, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("test_id", ["B06", "B17", "V01"])
async def test_single_empty_reply_matches_existing_null_probe_contract(test_id):
    reference, reference_calls = await execute_registered(test_id, None)
    actual, actual_calls = await execute_registered(test_id, "")
    assert reference_calls["judge"] > 0
    assert actual_calls == reference_calls
    assert actual.status is reference.status
    assert actual.score == reference.score
    assert len(actual.evidence) == len(reference.evidence)
    assert actual.error is None


@pytest.mark.asyncio
async def test_b16_five_empty_replies_remain_five_failures():
    result, calls = await execute_registered("B16", "", empty_count=5)
    assert calls["sut"] == 30
    assert calls["judge"] == 25
    assert len(result.evidence) == 30
    assert result.score == 25 / 30
    assert result.status is TestStatus.FAIL
    assert sum(item.extraction_error is not None for item in result.evidence) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("test_id", ["B17", "V01"])
async def test_custom_all_empty_inspection_remains_inconclusive(test_id):
    result, calls = await execute_registered(test_id, "", all_empty=True)
    assert result.status is TestStatus.INCONCLUSIVE
    failed_probes = [item for item in result.evidence
                     if item.extraction_error is not None]
    assert failed_probes
    assert all(not item.is_diagnostic for item in failed_probes)
    assert result.error and "Empty content" in result.error
    assert calls["sut"] > 1
    assert calls["judge"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", None])
async def test_tool_call_without_text_is_an_ordinary_response_error(content):
    reply = {"choices": [{"message": {
        "content": content,
        "tool_calls": [{"id": "call_1", "type": "function", "function": {
            "name": "lookup", "arguments": "{}",
        }}],
    }, "finish_reason": "tool_calls"}]}
    async with owned_endpoint(reply) as (endpoint, calls):
        provider = HttpProvider()
        try:
            with pytest.raises(ProviderResponseError) as caught:
                await provider.send_message(
                    [ChatMessage(role="user", content="lookup")],
                    ProviderConfig(provider="http", endpoint=endpoint, max_retries=0),
                )
            assert type(caught.value) is ProviderResponseError
            assert len(calls) == 1
        finally:
            await provider.aclose()
