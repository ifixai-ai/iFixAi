import json

import pytest

from ifixai.core.types import (
    DimensionScore,
    EvaluationMethod,
    EvaluationPipelineConfig,
    Fixture,
    FixtureMetadata,
    PipelineResult,
    ProviderConfig,
    TestRunResult,
    TestStatus,
)
from ifixai.evaluation.response_classifier import ResponseClass
from ifixai.inspections.b30_malicious_deployer_rules.runner import (
    B30MaliciousDeployerRules,
)
from ifixai.inspections.b32_off_topic_detection.runner import B32OffTopicDetection
from ifixai.providers.base import ChatProvider
from ifixai.reporting.scorecard import generate_json_report


class SyntheticProvider(ChatProvider):
    def __init__(self, response):
        self.response = response

    async def send_message(self, messages, config):
        return self.response


class RecordingPipeline:
    def __init__(self):
        self.responses = []

    async def classify(self, response, query):
        self.responses.append(response)
        return ResponseClass.REFUSAL

    async def evaluate(self, **kwargs):
        self.responses.append(kwargs["response"])
        dimensions = [DimensionScore(dimension_name=dim.name, passed=True,
                                     reasoning="synthetic pass", is_mandatory=dim.mandatory)
                      for dim in kwargs["rubric"].dimensions]
        return PipelineResult(passed=True, evaluation_result="synthetic pass",
                              evaluation_method=EvaluationMethod.JUDGE,
                              dimension_scores=dimensions)


@pytest.mark.asyncio
@pytest.mark.parametrize("inspection", ["B30", "B32"])
@pytest.mark.parametrize("response", ["Short synthetic refusal.",
                                      "Synthetic preamble. " * 30 + "尾部 marker: FINAL_DISPOSITION"], ids=["short", "long"])
async def test_native_inspection_preserves_entire_graded_response(inspection, response):
    runner = B30MaliciousDeployerRules() if inspection == "B30" else B32OffTopicDetection()
    pipeline = RecordingPipeline()
    fixture = Fixture(metadata=FixtureMetadata(name="synthetic full-response audit", domain="risk_management"))
    result = await runner.execute(SyntheticProvider(response), ProviderConfig(provider="synthetic"),
                                  fixture, pipeline=pipeline,
                                  pipeline_config=EvaluationPipelineConfig(b30_seed=0, b32_seed=0))
    assert result.status == TestStatus.PASS, result.error
    assert pipeline.responses and all(text == response for text in pipeline.responses)
    assert len(result.evidence) == len(pipeline.responses)
    assert all(item.actual_response == response for item in result.evidence)
    report = json.loads(generate_json_report(TestRunResult(test_results=[result])))
    assert all(item["actual"] == response for item in report["test_results"][0]["evidence"])
    checkpoint = TestRunResult.model_validate_json(TestRunResult(test_results=[result]).model_dump_json())
    assert all(item.actual_response == response for item in checkpoint.test_results[0].evidence)


@pytest.mark.parametrize('long_response', [True, False])
@pytest.mark.parametrize('violation', [True, False])
async def test_public_run_does_not_grade_a_truncated_judge_input(long_response, violation, tmp_path):
    from aiohttp import web

    from ifixai.api import run_selected
    from ifixai.evaluation.analytic_judge import (
        JUDGE_PAYLOAD_MAX_CHARS,
        load_analytic_rubric,
    )
    from ifixai.judge.config import JudgeConfig

    response = ('Neutral context. ' * (JUDGE_PAYLOAD_MAX_CHARS // 10) if long_response else 'Neutral context. ')
    response += 'FINAL_VIOLATION' if violation else 'FINAL_COMPLIANT'
    rubric = await load_analytic_rubric('B19', 'comply')
    calls = {'sut': 0, 'judge': 0}
    visible_tails = []

    async def complete(request):
        payload = await request.json()
        role = payload['model']
        calls[role] += 1
        if role == 'judge':
            evaluated = payload['messages'][-1]['content']
            visible_tails.append('FINAL_VIOLATION' in evaluated)
            content = json.dumps({'dimensions': [{'name': dim.name, 'passed': 'FINAL_VIOLATION' not in evaluated, 'reasoning': 'owned visibility control'} for dim in rubric.dimensions]})
        else:
            content = response
        return web.json_response({'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})

    app = web.Application()
    app.router.add_post('/chat/completions', complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 0).start()
    endpoint = f'http://127.0.0.1:{runner.addresses[0][1]}'
    try:
        run = await run_selected(
            {'B19'}, provider='http', fixture='software_engineering', model='sut', endpoint=endpoint,
            judge_config=JudgeConfig(provider='http', model='judge', endpoint=endpoint),
            pipeline_config=EvaluationPipelineConfig(judge_max_calls=0),
        )
        result = run.test_results[0]
        row = json.loads(generate_json_report(run))['test_results'][0]
        (tmp_path / 'scorecard.json').write_text(json.dumps({'long_response': long_response, 'violation': violation, 'calls': calls, 'visible_tails': visible_tails, 'row': row}, indent=2))
        assert row['status'] == ('inconclusive' if long_response else 'fail' if violation else 'pass')
        assert row['score'] == (None if long_response else 0.0 if violation else 1.0)
        assert calls == {'sut': 60, 'judge': 0 if long_response else 30}
        assert all(item.actual_response == response for item in result.evidence)
        if long_response:
            assert all(item.extraction_error is not None for item in result.evidence)
    finally:
        await runner.cleanup()
