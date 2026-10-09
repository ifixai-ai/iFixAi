"""Serialized real LangChain AIMessage outputs travel over actual aiohttp."""
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.runner import run_single
from ifixai.core.types import (
    ChatMessage,
    EvaluationPipelineConfig,
    ProviderConfig,
    TestStatus,
)
from ifixai.judge.config import JudgeConfig
from ifixai.providers.base import ProviderEmptyContentError
from ifixai.providers.langchain import LangChainProvider


@asynccontextmanager
async def invoke_endpoint(output):
    async def invoke(request):
        await request.json()
        return web.json_response({"output": output})
    app = web.Application()
    app.router.add_post("/invoke", invoke)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
    try:
        yield url
    finally:
        await runner.cleanup()


def config(url):
    return ProviderConfig(provider="langchain", endpoint=url, max_retries=0)


def serialized_message(content):
    messages = pytest.importorskip("langchain_core.messages")
    return messages.AIMessage(content=content).model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("content,expected", [
    (["Hello ", "world"], "Hello world"),
    ([{"type": "thinking", "thinking": "internal rationale"},
      {"type": "text", "text": "Visible answer"}], "Visible answer"),
    ([{"type": "text", "text": "A"}, "B", {"type": "text", "text": "C"}], "ABC"),
    ([{"type": "image_url", "image_url": {"url": "https://example.test/image"}},
      {"type": "text", "text": "  Café\n"}], "  Café\n"),
])
async def test_actual_serialized_ai_messages_return_only_ordered_text(content, expected):
    async with invoke_endpoint(serialized_message(content)) as url:
        result = await LangChainProvider().send_message(
            [ChatMessage(role="user", content="owned prompt")], config(url)
        )
        assert result == expected
        # The next conversation turn in BaseTest must accept the provider reply.
        assert ChatMessage(role="assistant", content=result).content == expected


@pytest.mark.asyncio
async def test_non_text_only_message_does_not_become_an_answer():
    output = serialized_message([{"type": "thinking", "thinking": "internal rationale"}])
    async with invoke_endpoint(output) as url:
        with pytest.raises(ProviderEmptyContentError):
            await LangChainProvider().send_message(
                [ChatMessage(role="user", content="owned prompt")], config(url)
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("output,expected", [
    ("legacy reply", "legacy reply"),
    ({"content": "legacy message", "type": "ai"}, "legacy message"),
    ({"structured": "opaque legacy output"}, "{'structured': 'opaque legacy output'}"),
])
async def test_existing_string_and_opaque_output_controls(output, expected):
    async with invoke_endpoint(output) as url:
        assert await LangChainProvider().send_message(
            [ChatMessage(role="user", content="owned prompt")], config(url)
        ) == expected


@pytest.mark.asyncio
async def test_shipped_b13_conversation_accepts_ai_message_content_blocks():
    output = serialized_message([{"type": "text", "text": "An owned trace"}])
    async with invoke_endpoint(output) as url:
        result = await run_single(
            "B13", LangChainProvider(), config(url),
            load_fixture("ifixai/fixtures/examples/customer_support.yaml"),
            judge_config=JudgeConfig(provider="mock"),
            pipeline_config=EvaluationPipelineConfig(),
        )
        assert result.status != TestStatus.ERROR
        assert result.error is None or not result.error
        assert result.evidence
        assert all(item.actual_response == "An owned trace" for item in result.evidence)
        assert all(item.evaluation_result != "error" for item in result.evidence)
