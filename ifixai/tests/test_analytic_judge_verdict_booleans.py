"""Exercise supported rubric verdict envelopes through real offline public runs."""

import json

import pytest
from aiohttp import web

from ifixai.api import run_selected
from ifixai.core.types import EvaluationMode, EvaluationPipelineConfig, TestStatus
from ifixai.evaluation.analytic_judge import (
    JudgeExtractionError,
    load_analytic_rubric,
    parse_rubric_verdict,
)
from ifixai.judge.config import JudgeConfig
from ifixai.reporting.scorecard import generate_json_report


async def _run_verdict_envelope(envelope, verdict, dimension_name=None):
    rubric = await load_analytic_rubric("B19", "comply")
    dimensions = [{"name": dim.name, "passed": verdict, "reasoning": "offline control"} for dim in rubric.dimensions]
    replies = {
        "dimensions": {"dimensions": dimensions},
        "alias": {"verdicts": dimensions},
        "bare-list": dimensions,
        "flat-dict": {dim.name: {"passed": verdict, "reasoning": "offline control"} for dim in rubric.dimensions},
        "flat-scalar": {dim.name: verdict for dim in rubric.dimensions},
    }
    if dimension_name is not None:
        dimensions[0]["name"] = dimension_name[0]
    calls = {"sut": 0, "judge": 0}

    async def complete(request):
        payload = await request.json()
        role = payload["model"]
        calls[role] += 1
        content = json.dumps(replies[envelope]) if role == "judge" else "The answer follows the supplied context."
        return web.json_response({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    app = web.Application()
    app.router.add_post("/chat/completions", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        endpoint = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        run = await run_selected(
            {"B19"}, provider="http", fixture="software_engineering", model="sut", endpoint=endpoint,
            judge_config=JudgeConfig(provider="http", model="judge", endpoint=endpoint),
            pipeline_config=EvaluationPipelineConfig(mode=EvaluationMode.FULL, judge_max_calls=0),
        )
        return run.test_results[0], calls, json.loads(generate_json_report(run))
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("envelope", ["dimensions", "alias", "bare-list", "flat-dict", "flat-scalar"])
@pytest.mark.parametrize("verdict,passed", [(False, False), (True, True), (0, False), (1, True), ("false", False), (" FALSE ", False), ("true", True)])
async def test_public_run_preserves_boolean_meaning_across_envelopes(envelope, verdict, passed):
    result, calls, report = await _run_verdict_envelope(envelope, verdict)
    assert calls["sut"] == 60 and calls["judge"] == 30
    assert result.status == (TestStatus.PASS if passed else TestStatus.FAIL)
    assert result.score == (1.0 if passed else 0.0)
    assert all(item.passed == passed and item.extraction_error is None for item in result.evidence)
    assert report["test_results"][0]["status"] == ("pass" if passed else "fail")
    assert report["test_results"][0]["score"] == (1.0 if passed else 0.0)


@pytest.mark.parametrize("first", [False, True])
async def test_duplicate_dimension_compares_normalized_verdicts(first):
    rubric = await load_analytic_rubric("B19", "comply")
    dimensions = [entry for dim in rubric.dimensions for entry in [
        {"name": dim.name, "passed": first}, {"name": dim.name, "passed": "false"},
    ]]
    raw = json.dumps({"dimensions": dimensions})
    if first:
        with pytest.raises(JudgeExtractionError, match="missing mandatory"):
            parse_rubric_verdict(raw, rubric)
    else:
        verdict = parse_rubric_verdict(raw, rubric)
        assert not verdict.passed
        assert verdict.weighted_score == 0.0


@pytest.mark.parametrize("envelope", ["dimensions", "alias", "bare-list", "flat-dict", "flat-scalar"])
async def test_unknown_verdict_is_unscored_in_public_run(envelope):
    result, calls, report = await _run_verdict_envelope(envelope, "not a verdict")
    assert calls["sut"] == 60 and calls["judge"] == 30
    assert result.status == TestStatus.INCONCLUSIVE
    assert all(item.extraction_error is not None for item in result.evidence)
    assert report["test_results"][0]["score"] is None


@pytest.mark.parametrize("token,passed", [
    ("pass", True), ("passed", True), ("true", True), ("yes", True), ("ok", True),
    ("fail", False), ("failed", False), ("false", False), ("no", False),
])
async def test_existing_verdict_tokens_are_preserved(token, passed):
    rubric = await load_analytic_rubric("B19", "comply")
    raw = json.dumps({"dimensions": [{"name": dim.name, "passed": token} for dim in rubric.dimensions]})
    assert parse_rubric_verdict(raw, rubric).passed == passed


async def test_missing_passed_retains_default_false():
    rubric = await load_analytic_rubric("B19", "comply")
    raw = json.dumps({"dimensions": [{"name": dim.name} for dim in rubric.dimensions]})
    verdict = parse_rubric_verdict(raw, rubric)
    assert not verdict.passed
    assert verdict.weighted_score == 0.0


@pytest.mark.parametrize("value", [-1, 2, 0.0, 1.0, None, [], {}])
async def test_other_numeric_or_structural_verdicts_remain_unscored(value):
    result, calls, report = await _run_verdict_envelope("dimensions", value)
    assert calls["sut"] == 60 and calls["judge"] == 30
    assert result.status == TestStatus.INCONCLUSIVE
    assert all(item.extraction_error is not None for item in result.evidence)
    assert report["test_results"][0]["score"] is None


@pytest.mark.parametrize("name", [None, 42, True, [], {}])
async def test_malformed_dimension_name_is_unscored_in_public_run(name, tmp_path):
    result, calls, report = await _run_verdict_envelope("dimensions", True, dimension_name=[name])
    (tmp_path / "scorecard.json").write_text(json.dumps({"name": name, "calls": calls, "row": report["test_results"][0]}, indent=2))
    assert result.status == TestStatus.INCONCLUSIVE
    assert calls["sut"] == 60 and calls["judge"] == 30
    assert all(item.extraction_error is not None for item in result.evidence)
    assert report["test_results"][0]["score"] is None
