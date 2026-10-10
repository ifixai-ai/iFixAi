"""LangServe message finish metadata must reach the strict judge contract."""

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderTruncatedError
from ifixai.providers.langchain import LangChainProvider
from ifixai.tests.test_langchain_content_blocks_native import invoke_endpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["partial verdict", "", [{"type": "text", "text": "partial verdict"}]])
async def test_strict_judge_rejects_reported_cutoff(content):
    output = {"type": "ai", "content": content, "response_metadata": {"finish_reason": "length"}}
    async with invoke_endpoint(output) as url:
        with pytest.raises(ProviderTruncatedError):
            await LangChainProvider().send_message([ChatMessage(content="grade")],
                ProviderConfig(provider="langchain", endpoint=url, reject_truncated=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,reject", [("length", False), ("stop", True)])
async def test_ordinary_sut_and_complete_judge_preserve_visible_text(reason, reject):
    output = {"type": "ai", "content": "visible reply", "response_metadata": {"finish_reason": reason}}
    async with invoke_endpoint(output) as url:
        assert await LangChainProvider().send_message([ChatMessage(content="prompt")],
            ProviderConfig(provider="langchain", endpoint=url, reject_truncated=reject)) == "visible reply"


@pytest.mark.asyncio
async def test_legacy_output_without_metadata_remains_supported():
    async with invoke_endpoint({"content": "legacy reply"}) as url:
        assert await LangChainProvider().send_message([ChatMessage(content="prompt")],
            ProviderConfig(provider="langchain", endpoint=url, reject_truncated=True)) == "legacy reply"
