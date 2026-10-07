"""Typed empty HTTP replies drop a probe, and all-empty inspections stay unscorable."""

import asyncio
import json
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
    TestRunResult,
    TestStatus,
)
from ifixai.evaluation.analytic_judge import load_analytic_rubric
from ifixai.judge.config import JudgeConfig
from ifixai.providers.base import (
    ProviderEmptyContentError,
    ProviderResponseError,
    ProviderTruncatedError,
)
from ifixai.providers.http import HttpProvider
from ifixai.reporting.health import run_health


def completion(content="", finish_reason="stop"):
    return {
        "choices": [{
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
    }


@asynccontextmanager
async def owned_endpoint(reply):
    requests = []

    async def respond(request):
        requests.append((request.path, await request.json()))
        return aiohttp.web.json_response(reply)

    app = aiohttp.web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    app.router.add_post("/v1/retrieve", respond)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1", requests
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, expected",
    [
        ({}, ProviderResponseError),
        ({"choices": []}, ProviderResponseError),
        ({"choices": [{}]}, ProviderResponseError),
        ({"choices": [{"message": {}}]}, ProviderResponseError),
        (completion(None), ProviderResponseError),
        (completion([]), ProviderResponseError),
        ({"error": {"code": 429, "message": "rate limited"}}, ProviderResponseError),
        ({"choices": [{"message": {"content": ""}, "finish_reason": "error",
                       "error": {"code": 429, "message": "rate limited"}}]}, ProviderResponseError),
        (dict(completion(), error={"code": 429, "message": "rate limited"}), ProviderResponseError),
        (completion("", "length"), ProviderTruncatedError),
        (completion("ordinary"), "ordinary"),
        (completion(), ProviderEmptyContentError),
        (completion("", "ERROR"), ProviderResponseError),
        ({"choices": [{"message": {"content": ""}, "error": {}}]}, ProviderResponseError),
    ],
)
async def test_empty_completion_keeps_other_response_failures_distinct(reply, expected):
    async with owned_endpoint(reply) as (endpoint, requests):
        provider = HttpProvider()
        config = ProviderConfig(
            provider="http", model="owned", endpoint=endpoint,
            max_retries=1, timeout=5, reject_truncated=True,
        )
        try:
            if isinstance(expected, str):
                assert await provider.send_message([ChatMessage(content="hello")], config) == expected
            else:
                with pytest.raises(expected) as caught:
                    await provider.send_message([ChatMessage(content="hello")], config)
                assert type(caught.value) is expected
                assert caught.value.provider == "http"
                assert caught.value.endpoint == endpoint
            assert len(requests) == 1
        finally:
            await provider.aclose()


@pytest.mark.asyncio
async def test_public_all_empty_inspection_has_no_false_communication_evidence():
    async with owned_endpoint(completion()) as (endpoint, requests):
        result = await run_single(
            "B06", provider=HttpProvider(), fixture="default", endpoint=endpoint,
            model="owned", timeout=5, max_retries=0,
        )
        assert result.status is TestStatus.INCONCLUSIVE
        assert result.error and "Empty content" in result.error
        assert result.evidence == []
        assert Counter(path for path, _ in requests) == {
            "/v1/retrieve": 1, "/v1/chat/completions": 44,
        }
        health = run_health(TestRunResult(test_results=[result]))
        assert health.unreachable == 0


@asynccontextmanager
async def inspection_endpoint(bad_content="", bad_at=2, all_bad=False, passed=True):
    requests = []
    rubric = await load_analytic_rubric("B06", "signal_uncertainty")

    async def respond(request):
        payload = await request.json()
        requests.append((request.path, payload))
        if request.path.endswith("retrieve"):
            return aiohttp.web.json_response({"results": []})
        model = payload["model"]
        number = sum(path == request.path and body.get("model") == model for path, body in requests)
        content = "I am uncertain about the next quarter estimate. Please seek manual review."
        if model == "judge":
            content = json.dumps({"dimensions": [
                {"name": dim.name, "passed": passed, "reasoning": "owned synthetic grading control"}
                for dim in rubric.dimensions
            ]})
        elif model == "all-empty" or all_bad or number == bad_at:
            content = bad_content
        return aiohttp.web.json_response(completion(content))

    app = aiohttp.web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    app.router.add_post("/v1/retrieve", respond)
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1", requests
    finally:
        await runner.cleanup()


async def run_b06(endpoint, model="sut"):
    return await run_single(
        "B06", provider=HttpProvider(), fixture="default", endpoint=endpoint,
        model=model, timeout=5, max_retries=0,
        judge_config=JudgeConfig(provider="http", endpoint=endpoint, model="judge"),
        pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_content,bad_at,all_bad,passed", [
    ("", 1, False, True), ("", 2, False, True), ("", 44, False, True),
    (None, 2, False, True), ("", 0, True, True), (None, 0, True, True),
    ("", 0, False, True), ("", 2, False, False),
])
async def test_public_b06_preserves_surviving_probes(bad_content, bad_at, all_bad, passed):
    async with inspection_endpoint(bad_content, bad_at, all_bad, passed) as (endpoint, requests):
        result = await run_b06(endpoint)
        calls = Counter(body.get("model", "retrieve") for _, body in requests)
        measured = [item for item in result.evidence if item.extraction_error is None]
        health = run_health(TestRunResult(test_results=[result]))
        assert calls["sut"] == 44
        assert calls["retrieve"] == 1
        expected_graded = 0 if all_bad else (43 if bad_at else 44)
        assert len(measured) == calls["judge"] == expected_graded
        assert health.unreachable == ((44 if all_bad else 1) if bad_content is None else 0)
        assert health.judge_broke == 0
        if all_bad:
            assert result.status is TestStatus.INCONCLUSIVE
            assert health.invalid
            if bad_content == "":
                assert result.evidence == []
                assert result.error and "Empty content" in result.error
            else:
                assert len(result.evidence) == 44
                assert result.error is None
        else:
            assert result.status is (TestStatus.PASS if passed else TestStatus.FAIL)
            assert result.score == (1.0 if passed else 0.0)
            assert not health.invalid
            assert result.error is None


@pytest.mark.asyncio
async def test_empty_accounting_is_isolated_between_concurrent_and_repeated_inspections():
    async with inspection_endpoint() as (endpoint, requests):
        empty, mixed = await asyncio.gather(run_b06(endpoint, "all-empty"), run_b06(endpoint, "mixed"))
        assert empty.status is TestStatus.INCONCLUSIVE
        assert empty.error and "Empty content" in empty.error
        assert empty.evidence == []
        assert mixed.status is TestStatus.PASS
        assert len(mixed.evidence) == 43
        # The same registered inspection runs again after both return paths.
        repeated = await run_b06(endpoint, "repeated")
        assert repeated.status is TestStatus.PASS
        assert len(repeated.evidence) == 43
        calls = Counter(body.get("model", "retrieve") for _, body in requests)
        assert calls == {"all-empty": 44, "mixed": 44, "repeated": 44, "judge": 86, "retrieve": 3}
