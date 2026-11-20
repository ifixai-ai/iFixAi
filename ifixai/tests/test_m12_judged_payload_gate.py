"""M12 declines an arc whose transcript the judge would receive truncated.

`sanitize_response_payload` escapes every `USER:` / `ASSISTANT:` line start (+2 chars each) before it
truncates from the end, so the judged string is longer than the rendered one. A rendered transcript just
under the ceiling would lose its last turn, the handoff the exit veto reads. The gate measures what the
judge is sent, as V01 and V02 do."""

import asyncio

from ifixai.core.types import (
    DimensionScore,
    EvaluationMethod,
    JudgeErrorKind,
    PipelineResult,
    RubricVerdict,
    User,
)
from ifixai.evaluation.analytic_judge import (
    load_analytic_rubric,
    sanitize_response_payload,
)
from ifixai.inspections.judge_probe import TrajectoryTurn, format_trajectory
from ifixai.inspections.m12_end_user_dependency_cultivation.runner import (
    M12EndUserDependencyCultivation,
)
from ifixai.inspections.m12_end_user_dependency_cultivation.runner_constants import (
    _MAX_JUDGE_PAYLOAD_CHARS,
    _OUTCOME,
    ARCS,
)
from ifixai.inspections.m12_end_user_dependency_cultivation.runner_types import (
    TrajectorySpec,
)

HANDOFF = "HANDOFF-PACKET"


class _RecordingJudge:
    """Passes every dimension and keeps what the real judge would be sent."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def is_ensemble_judge(self):
        return False

    async def evaluate(self, response, criteria, rubric, references=None, context="",
                       context_vars=None, **_kwargs):
        self.seen.append(sanitize_response_payload(response))
        dimensions = [
            DimensionScore(dimension_name=d.name, reasoning="owned", is_mandatory=d.mandatory, passed=True)
            for d in rubric.dimensions
        ]
        return PipelineResult(
            passed=True, evaluation_result="pass", evaluation_method=EvaluationMethod.JUDGE,
            dimension_scores=dimensions,
            rubric_verdict=RubricVerdict(dimension_scores=dimensions, weighted_score=1.0,
                                         passed=True, verdict="pass"),
        )


def _arc(rendered_chars: int) -> list[TrajectoryTurn]:
    """A 7-turn arc whose rendered transcript is `rendered_chars` long, the handoff in turn 7."""
    turns = [TrajectoryTurn(turn=i, user_msg=f"u{i}", assistant_msg="a") for i in range(1, 7)]
    turns.append(TrajectoryTurn(turn=7, user_msg="u7", assistant_msg=f"a {HANDOFF}"))
    pad = rendered_chars - len(format_trajectory(turns))
    turns[0] = TrajectoryTurn(turn=1, user_msg="u1", assistant_msg="a" + "x" * pad)
    assert len(format_trajectory(turns)) == rendered_chars
    return turns


def _score(trajectory: list[TrajectoryTurn]):
    inspection = M12EndUserDependencyCultivation()
    judge = _RecordingJudge()
    inspection._pipeline = judge
    rubric = asyncio.run(load_analytic_rubric("M12", _OUTCOME))
    spec = TrajectorySpec(arc=ARCS[0], user=User(user_id="u", name="U", roles=["r"]), user_index=0)
    return asyncio.run(inspection._score_arc(spec, trajectory, rubric)), judge


def test_an_arc_the_judge_would_see_truncated_is_declined_not_judged():
    trajectory = _arc(_MAX_JUDGE_PAYLOAD_CHARS - 5)
    assert len(sanitize_response_payload(format_trajectory(trajectory))) > _MAX_JUDGE_PAYLOAD_CHARS

    item, judge = _score(trajectory)

    assert judge.seen == []
    assert item.extraction_error is JudgeErrorKind.CONTRACT
    assert item.details["blob_chars"] <= _MAX_JUDGE_PAYLOAD_CHARS < item.details["judged_chars"]


def test_an_arc_that_fits_once_judged_is_still_judged_whole():
    trajectory = _arc(_MAX_JUDGE_PAYLOAD_CHARS - 100)

    item, judge = _score(trajectory)

    assert item.extraction_error is None
    assert judge.seen
    assert all(HANDOFF in sent for sent in judge.seen)
