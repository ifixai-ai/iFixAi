"""Owned HTTP completions exercise the actual optional Anthropic SDK."""

import asyncio
import importlib
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ProviderTruncatedError


@contextmanager
def completion_server(text, stop_reason):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = {
                "id": "owned-message",
                "type": "message",
                "role": "assistant",
                "model": "owned-model",
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "content": [{"type": "text", "text": text}] if text else [],
            }
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def send(endpoint, reject_truncated):
    pytest.importorskip("anthropic")
    provider_class = importlib.import_module(
        "ifixai.providers.anthropic"
    ).AnthropicProvider

    async def exercise():
        provider = provider_class()
        try:
            return await provider.send_message(
                [ChatMessage(role="user", content="Owned judge probe")],
                ProviderConfig(
                    provider="anthropic",
                    endpoint=endpoint,
                    api_key="synthetic-local-key",
                    model="owned-model",
                    timeout=5,
                    max_retries=0,
                    reject_truncated=reject_truncated,
                ),
            )
        finally:
            await provider.aclose()

    return asyncio.run(exercise())


@pytest.mark.parametrize("text", ['{"score": 1}', ""])
def test_cutoff_judge_replies_raise_the_shared_truncation_error(text):
    with completion_server(text, "max_tokens") as endpoint:
        with pytest.raises(ProviderTruncatedError):
            send(endpoint, reject_truncated=True)


def test_cutoff_sut_reply_remains_observable_by_default():
    with completion_server("The partial SUT behavior", "max_tokens") as endpoint:
        assert send(endpoint, reject_truncated=False) == "The partial SUT behavior"


def test_completed_judge_reply_is_returned_normally():
    with completion_server('{"score": 1}', "end_turn") as endpoint:
        assert send(endpoint, reject_truncated=True) == '{"score": 1}'


@pytest.mark.parametrize("stop_reason", ["max_tokens", "end_turn"])
def test_actual_judge_pipeline_does_not_grade_a_cutoff_as_pass(stop_reason):
    pytest.importorskip("anthropic")
    from ifixai.core.types import (
        AnalyticRubric,
        EvaluationCriteria,
        EvaluationPipelineConfig,
        RubricDimension,
    )
    from ifixai.evaluation.analytic_judge import AnalyticRubricJudge
    from ifixai.evaluation.pipeline import EvaluationPipeline
    from ifixai.judge.config import JudgeConfig
    from ifixai.judge.evaluator import JudgeEvaluator

    verdict = json.dumps({"dimensions": [{"name": "complete", "passed": True,
                                        "reasoning": "owned fixture"}],
                          "overall_reasoning": "owned fixture"})
    rubric = AnalyticRubric(test_id="B13", outcome_type="comply", dimensions=[
        RubricDimension(name="complete", description="Complete trace", weight=1.0)
    ])
    with completion_server(verdict, stop_reason) as endpoint:
        async def exercise():
            judge = JudgeEvaluator(JudgeConfig(provider="anthropic", endpoint=endpoint,
                                              api_key="synthetic-local-key", model="owned-model"))
            try:
                pipeline = EvaluationPipeline(EvaluationPipelineConfig(),
                                              judge=AnalyticRubricJudge(judge))
                result = await pipeline.evaluate("owned trace", EvaluationCriteria(), rubric)
                if stop_reason == "max_tokens":
                    assert not result.passed
                    assert result.rubric_verdict is None
                    assert "ProviderTruncatedError" in result.evaluation_result
                    assert judge.get_stats()["judge_transport_failures"] == {"owned-model": 1}
                else:
                    assert result.passed
                    assert result.rubric_verdict.weighted_score == 1.0
                    assert judge.get_stats()["judge_transport_failures"] == {}
            finally:
                await judge.aclose()
        asyncio.run(exercise())
