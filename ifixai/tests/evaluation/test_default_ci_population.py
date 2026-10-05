from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from ifixai.api import run_single
from ifixai.core.types import (
    EvaluationMode,
    EvaluationPipelineConfig,
    EvidenceItem,
    JudgeErrorKind,
)
from ifixai.evaluation.analytic_judge import load_analytic_rubric
from ifixai.inspections.b16_silent_failure.runner import B16SilentFailure
from ifixai.judge.config import JudgeConfig
from ifixai.scoring.engine import compute_test_ci


@asynccontextmanager
async def flaky_http_judge(rubric):
    calls = []
    async def reply(request):
        calls.append(await request.json())
        if len(calls) <= 3:
            content = 'not JSON'
        else:
            import json
            content = json.dumps({'dimensions': [{'name': d.name, 'passed': True} for d in rubric.dimensions]})
        return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})
    app = web.Application()
    app.router.add_post('/v1/chat/completions', reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    try:
        yield f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1', calls
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_public_b06_ci_matches_the_scoreable_population_after_judge_failure():
    rubric = await load_analytic_rubric('B06', 'signal_uncertainty')
    async with flaky_http_judge(rubric) as (endpoint, calls):
        result = await run_single(
            'B06', provider='mock', fixture='customer_support',
            judge_config=JudgeConfig(provider='http', endpoint=endpoint),
            pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL, judge_max_calls=500),
        )
    measured = [e for e in result.evidence if e.extraction_error is None]
    failed_judge = [e for e in result.evidence if e.extraction_error is not None]
    assert len(failed_judge) == 1
    assert result.score == 1.0 and len(measured) >= 10
    assert len(calls) == len(measured) + 3, 'the first probe exhausted three extraction attempts'
    assert result.confidence_interval == compute_test_ci(measured), (
        result.score, len(measured), result.confidence_interval.model_dump(),
        compute_test_ci(measured).model_dump(),
    )
    assert result.confidence_interval.sample_size == len(measured)


def test_explicit_fail_closed_inspections_keep_errors_in_the_ci_population():
    evidence = [EvidenceItem(test_case_id='ok', passed=True), EvidenceItem(
        test_case_id='failed-extraction', passed=False, extraction_error=JudgeErrorKind.EXTRACTION,
    )]
    inspection = B16SilentFailure()
    assert inspection.spec.count_extraction_errors_as_fail
    assert inspection.ci_evidence(evidence) == evidence
    assert compute_test_ci(inspection.ci_evidence(evidence)).sample_size == 2


def test_repeated_valid_responses_remain_in_the_default_population():
    from ifixai.inspections.b06_uncertainty_signalling.runner import (
        B06UncertaintySignalling,
    )

    repeated = EvidenceItem(test_case_id='same-input', prompt_sent='same prompt', passed=True)
    evidence = [repeated, repeated, repeated]
    inspection = B06UncertaintySignalling()
    assert inspection.ci_evidence(evidence) == evidence
    assert compute_test_ci(inspection.ci_evidence(evidence)).sample_size == 3
