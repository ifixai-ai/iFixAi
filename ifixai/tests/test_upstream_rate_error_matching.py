from types import SimpleNamespace

import pytest

from ifixai.providers.base import (
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderResponseError,
    raise_if_choice_errored,
)


@pytest.mark.parametrize('code,message,error', [(503, 'Could not generate a response', ProviderOverloadedError), (400, 'Rejected by corporate policy', ProviderResponseError), (None, 'rate limit exceeded', ProviderRateLimitError), (429, 'Too many requests', ProviderRateLimitError)])
def test_upstream_error_type_does_not_match_rate_inside_other_words(code, message, error):
    choice = SimpleNamespace(finish_reason='error', error={'code': code, 'message': message})
    with pytest.raises(error):
        raise_if_choice_errored('owned', 'owned-endpoint', choice, 'partial')


@pytest.mark.parametrize("code,message", [(502, "You are being rate limited"), (400, "rate_limit_exceeded")])
def test_real_rate_limit_variants_preserve_retryable_rate_classification(code, message):
    choice = SimpleNamespace(finish_reason='error', error={'code': code, 'message': message})
    with pytest.raises(ProviderRateLimitError):
        raise_if_choice_errored('owned', 'owned-endpoint', choice, 'partial')


@pytest.mark.parametrize("message", ["userRateLimitExceeded", "upstream_rate_limit_exceeded"])
def test_prefixed_rate_limit_codes_keep_retryable_classification(message):
    choice = SimpleNamespace(finish_reason="error", error={"code": 400, "message": message})
    with pytest.raises(ProviderRateLimitError):
        raise_if_choice_errored("owned", "owned-endpoint", choice, "partial")


@pytest.mark.parametrize("message", [
    "Request rate too high",
    "Too many requests",
    "Quota exceeded for quota metric 'Generate Content API requests per minute'",
])
def test_rate_and_per_minute_quota_messages_remain_retryable(message):
    choice = SimpleNamespace(finish_reason="error", error={"code": 400, "message": message})
    with pytest.raises(ProviderRateLimitError):
        raise_if_choice_errored("owned", "owned-endpoint", choice, "partial")


@pytest.mark.parametrize("message", ["not accurate", "cannot generate a response"])
def test_plain_rate_substrings_are_not_rate_limit_codes(message):
    choice = SimpleNamespace(finish_reason="error", error={"code": 400, "message": message})
    with pytest.raises(ProviderResponseError):
        raise_if_choice_errored("owned", "owned-endpoint", choice, "partial")


@pytest.mark.parametrize("message", [
    "userRateLimitExceeded",
    "upstream_rate_limit_exceeded",
    "Request rate too high",
    "Too many requests",
    "Quota exceeded for quota metric 'Generate Content API requests per minute'",
])
async def test_native_judge_recovers_from_rate_limit_message(message):
    import json

    from ifixai.api import run_selected
    from ifixai.core.types import EvaluationMode, EvaluationPipelineConfig, TestStatus
    from ifixai.evaluation.analytic_judge import load_analytic_rubric
    from ifixai.judge.config import JudgeConfig
    from ifixai.reporting.scorecard import generate_json_report
    from ifixai.tests.test_http_upstream_choice_errors import endpoint

    rubric = await load_analytic_rubric("B19", "comply")
    answer = json.dumps({"dimensions": [{"name": d.name, "passed": True, "reasoning": "owned offline control"} for d in rubric.dimensions]})
    calls = []

    def choice():
        calls.append(1)
        if len(calls) == 1:
            return {"message": {"content": "partial"}, "finish_reason": "error", "error": {"code": 400, "message": message}}
        return {"message": {"content": answer}, "finish_reason": "stop"}

    async with endpoint(choice) as url:
        result = await run_selected(
            {"B19"}, provider="mock", fixture="software_engineering", model="owned-sut",
            judge_config=JudgeConfig(provider="http", endpoint=url, model="owned-judge"),
            pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL, judge_max_calls=0),
        )
    scorecard = json.loads(generate_json_report(result))["test_results"][0]
    assert result.test_results[0].status == TestStatus.PASS
    assert scorecard["score"] == 1.0
    assert len(result.test_results[0].evidence) == 30
    assert all(item.extraction_error is None for item in result.test_results[0].evidence)
    assert len(calls) == 31
    print(json.dumps({"native_offline_scorecard": {"test_id": "B19", "status": scorecard["status"], "score": scorecard["score"], "graded_items": 30, "native_judge_requests": len(calls)}}))


@pytest.mark.parametrize("message", [
    "Request rate too high",
    "Too many requests",
    "Quota exceeded for quota metric 'Generate Content API requests per minute'",
])
async def test_native_http_agent_retries_rate_limit_message(message):
    from ifixai.core.types import ChatMessage, ProviderConfig
    from ifixai.providers.http import HttpProvider
    from ifixai.tests.test_http_upstream_choice_errors import endpoint

    calls = []

    def choice():
        calls.append(1)
        if len(calls) == 1:
            return {"message": {"content": "partial"}, "finish_reason": "error",
                    "error": {"code": 400, "message": message}}
        return {"message": {"content": "completed answer"}, "finish_reason": "stop"}

    provider = HttpProvider()
    try:
        async with endpoint(choice) as url:
            response = await provider.send_message(
                [ChatMessage(content="hello")],
                ProviderConfig(provider="http", endpoint=url, max_retries=1, retry_base_delay=0),
            )
        assert response == "completed answer"
        assert len(calls) == 2
    finally:
        await provider.aclose()
