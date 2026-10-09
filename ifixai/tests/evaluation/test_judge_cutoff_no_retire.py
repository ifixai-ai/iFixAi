"""A cut-off judge reply costs one probe its model; only 3 in a row retire it."""

import json
from pathlib import Path

import pytest

from ifixai.core.types import (
    AnalyticRubric,
    ChatMessage,
    EvaluationCriteria,
    EvaluationPipelineConfig,
    PipelineResult,
    ProviderConfig,
    RubricDimension,
)
from ifixai.evaluation import analytic_judge
from ifixai.evaluation.analytic_judge import AnalyticRubricJudge, JudgeErrorKind
from ifixai.evaluation.pipeline import EvaluationPipeline
from ifixai.judge import evaluator
from ifixai.judge.config import JudgeConfig
from ifixai.judge.evaluator import JudgeEvaluator
from ifixai.providers.base import (
    ProviderError,
    ProviderOverloadedError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTruncatedError,
)
from ifixai.reporting.scorecard import judge_substitution_warnings

RUBRIC = AnalyticRubric(
    test_id="B13",
    outcome_type="comply",
    dimensions=[RubricDimension(name="complete", description="Complete trace", weight=1.0)],
)
VERDICT = json.dumps(
    {
        "dimensions": [{"name": "complete", "passed": True, "reasoning": "owned fixture"}],
        "overall_reasoning": "owned fixture",
    }
)


class ScriptedProvider:
    """Fake judge transport: each model answers from its own scripted queue."""

    def __init__(self, replies: dict[str, list[str | type[ProviderError]]]) -> None:
        self.replies = replies
        self.calls: list[str | None] = []

    async def send_message(self, messages: list[ChatMessage], config: ProviderConfig) -> str:
        self.calls.append(config.model)
        reply = self.replies[str(config.model)].pop(0)
        if isinstance(reply, str):
            return reply
        raise reply(provider="owned", details="scripted judge failure")


def make_judge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: ScriptedProvider,
    fallbacks: list[str],
) -> JudgeEvaluator:
    policy = tmp_path / "judge-fallbacks.json"
    chain = {"attempts_per_model": 2, "models": [{"model": m} for m in fallbacks]}
    policy.write_text(json.dumps({"providers": {"http": chain}}), encoding="utf-8")
    monkeypatch.setenv("IFIXAI_JUDGE_FALLBACKS", str(policy))
    monkeypatch.setattr(evaluator, "resolve_provider", lambda _name: provider)
    monkeypatch.setattr(analytic_judge, "_BACKOFF_BASE", 0.0)
    return JudgeEvaluator(JudgeConfig(provider="http", model="primary"))


async def grade(judge: JudgeEvaluator, probes: int) -> list[PipelineResult]:
    pipeline = EvaluationPipeline(
        EvaluationPipelineConfig(), judge=AnalyticRubricJudge(judge)
    )
    return [
        await pipeline.evaluate("owned trace", EvaluationCriteria(), RUBRIC)
        for _ in range(probes)
    ]


async def test_cutoff_on_lone_judge_drops_only_that_probe(monkeypatch, tmp_path):
    provider = ScriptedProvider({"primary": [ProviderTruncatedError] + [VERDICT] * 4})
    results = await grade(make_judge(monkeypatch, tmp_path, provider, []), probes=5)

    assert [r.extraction_error for r in results] == [JudgeErrorKind.EXTRACTION] + [None] * 4
    assert [r.passed for r in results] == [False, True, True, True, True]
    assert "ProviderTruncatedError" in results[0].evaluation_result
    # The cut-off prompt is never re-sent to the same model.
    assert provider.calls == ["primary"] * 5


async def test_cutoff_sends_one_probe_to_fallback_then_primary_again(monkeypatch, tmp_path):
    provider = ScriptedProvider(
        {"primary": [ProviderTruncatedError, VERDICT], "backup": [VERDICT, VERDICT]}
    )
    judge = make_judge(monkeypatch, tmp_path, provider, ["backup"])
    results = await grade(judge, probes=2)

    assert [(r.passed, r.extraction_error) for r in results] == [(True, None), (True, None)]
    assert provider.calls == ["primary", "backup", "primary"]
    assert judge.get_stats()["fallback_grades"] == {"backup": 1}
    # The primary graded the next probe, so the report must not call it unreachable.
    assert judge_substitution_warnings(judge.get_stats())[0] == (
        "substitute judge graded this run: backup (1 verdict). primary gave no verdict on "
        "those probes (a failed call or a cut-off reply). "
        "Scores are not comparable to a run graded by the configured judge."
    )


@pytest.mark.parametrize(
    "failure", [ProviderTimeoutError, ProviderOverloadedError, ProviderResponseError]
)
async def test_model_that_burns_its_budget_is_still_retired(monkeypatch, tmp_path, failure):
    provider = ScriptedProvider(
        {"primary": [failure, failure], "backup": [VERDICT, VERDICT]}
    )
    results = await grade(make_judge(monkeypatch, tmp_path, provider, ["backup"]), probes=2)

    assert [(r.passed, r.extraction_error) for r in results] == [(True, None), (True, None)]
    assert provider.calls == ["primary", "primary", "backup", "backup"]


CUT = ProviderTruncatedError
E = JudgeErrorKind.EXTRACTION


@pytest.mark.parametrize(
    ("replies", "probes", "errors"),
    [
        # Every reply cut off: retired on the 3rd, the rest drop with no call.
        ([CUT] * 3, 5, [E] * 5),
        # A verdict between cut-offs resets the count, so it is never retired.
        ([CUT, CUT, VERDICT] * 3, 9, [E, E, None] * 3),
    ],
)
async def test_lone_judge_retired_only_after_three_cutoffs_in_a_row(
    monkeypatch, tmp_path, replies, probes, errors
):
    provider = ScriptedProvider({"primary": list(replies)})
    results = await grade(make_judge(monkeypatch, tmp_path, provider, []), probes=probes)

    assert [r.extraction_error for r in results] == errors
    assert provider.calls == ["primary"] * len(replies)


async def test_primary_that_always_cuts_off_stops_being_billed(monkeypatch, tmp_path):
    provider = ScriptedProvider({"primary": [CUT] * 3, "backup": [VERDICT] * 5})
    judge = make_judge(monkeypatch, tmp_path, provider, ["backup"])
    results = await grade(judge, probes=5)

    assert [(r.passed, r.extraction_error) for r in results] == [(True, None)] * 5
    assert provider.calls == ["primary", "backup"] * 3 + ["backup"] * 2
    assert judge.get_stats()["fallback_grades"] == {"backup": 5}


async def test_each_cutoff_is_logged_with_its_streak(monkeypatch, tmp_path, caplog):
    provider = ScriptedProvider({"primary": [CUT] * 3})
    await grade(make_judge(monkeypatch, tmp_path, provider, []), probes=3)

    detail = "[owned] scripted judge failure (endpoint: )"
    assert [r.getMessage() for r in caplog.records if r.name == analytic_judge.__name__] == [
        f"Judge model primary cut off its reply on B13 (1/3 in a row), "
        f"skipping it for this probe: {detail}",
        f"Judge model primary cut off its reply on B13 (2/3 in a row), "
        f"skipping it for this probe: {detail}",
        f"Retiring judge model primary for this run after 3 cut-offs in a row "
        f"(last on B13): {detail}",
    ]
