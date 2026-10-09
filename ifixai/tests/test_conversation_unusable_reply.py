"""What the harness does with an agent turn that produced no usable reply.

Two rules, both about not grading something the agent never said:

1. A conversation is not continued past a turn the agent did not answer. The next prompt would
   be sent on a history with a hole in it, and its reply graded as if the plan had run.
2. An inspection in which every prompt went unanswered is INCONCLUSIVE, even if API-level items
   that need no reply were graded. Those items alone are not what the inspection measures.
"""

from typing import TypedDict

import pytest

from ifixai.core.types import (
    AnalyticRubric,
    ChatMessage,
    ConversationPlan,
    EvaluationMethod,
    EvidenceItem,
    Fixture,
    FixtureMetadata,
    InspectionCategory,
    InspectionMethod,
    InspectionSpec,
    InspectionStep,
    JudgeErrorKind,
    PipelineResult,
    ProviderConfig,
    TestStatus,
)
from ifixai.evaluation.analytic_judge import load_analytic_rubric
from ifixai.harness.base import BaseTest, has_no_answered_prompt
from ifixai.providers.base import (
    ChatProvider,
    ProviderEmptyContentError,
    ProviderError,
    ProviderTimeoutError,
)
from ifixai.shared.evidence import build_unreachable_item

SPEC = InspectionSpec(
    test_id="B99",
    name="Unusable reply handling",
    category=InspectionCategory.FABRICATION,
    description="harness contract",
    threshold=0.5,
    weight=1.0,
    scoring_method="ratio",
    min_evidence_items=1,
)
THREE_STEP_PLAN = ConversationPlan(
    test_id="B99",
    steps=[
        InspectionStep(step_id=1, prompt_template="first question"),
        InspectionStep(step_id=2, prompt_template="second question"),
        InspectionStep(step_id=3, prompt_template="third question"),
    ],
)
FIXTURE = Fixture(metadata=FixtureMetadata(name="t", version="1.0", domain="general"))
CONFIG = ProviderConfig(provider="stub")


class ScriptedTurn(TypedDict):
    reply: str
    error: ProviderError | None


class ScriptedProvider(ChatProvider):
    """Answers each call from a script; an entry with an error raises it instead."""

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        self.turns = turns
        self.histories: list[list[ChatMessage]] = []

    async def send_message(self, messages: list[ChatMessage], config: ProviderConfig) -> str:
        turn = self.turns[len(self.histories)]
        self.histories.append(list(messages))
        if turn["error"] is not None:
            raise turn["error"]
        return turn["reply"]


class PassingPipeline:
    """Grades every reply it is shown as a pass."""

    async def evaluate(self, **_: object) -> PipelineResult:
        return PipelineResult(
            passed=True, evaluation_result="pass", evaluation_method=EvaluationMethod.JUDGE
        )


class ConversationInspection(BaseTest):
    """Runs one three-step conversation, then adds any extra evidence it was given."""

    def __init__(self, rubric: AnalyticRubric, extra_evidence: list[EvidenceItem]) -> None:
        super().__init__(SPEC)
        self.rubric = rubric
        self.extra_evidence = extra_evidence

    async def run(
        self, provider: ChatProvider, config: ProviderConfig, fixture: Fixture
    ) -> list[EvidenceItem]:
        conversation = await self.execute_conversation(
            provider,
            config,
            THREE_STEP_PLAN,
            {"case_id": "case"},
            pipeline=PassingPipeline(),
            rubric_override=self.rubric,
        )
        return [*conversation, *self.extra_evidence]


def answered(reply: str) -> ScriptedTurn:
    return {"reply": reply, "error": None}


def unanswered(error: ProviderError) -> ScriptedTurn:
    return {"reply": "", "error": error}


def empty_reply() -> ProviderEmptyContentError:
    return ProviderEmptyContentError(
        provider="stub", endpoint="loopback", details="Empty content in response"
    )


def structural_pass() -> EvidenceItem:
    return EvidenceItem(
        test_case_id="B99-struct-1",
        description="API-level check that needs no reply",
        expected="denied",
        actual="denied",
        passed=True,
        inspection_method=InspectionMethod.STRUCTURAL,
        evaluation_method=EvaluationMethod.STRUCTURAL,
    )


def graded_reply() -> EvidenceItem:
    return EvidenceItem(
        test_case_id="B99_step1_case",
        description="graded reply",
        prompt_sent="a question",
        actual_response="an answer",
        passed=True,
    )


def unreachable(test_case_id: str) -> EvidenceItem:
    return build_unreachable_item(
        test_case_id=test_case_id,
        description="probe",
        prompt_sent="a question",
        expected="an answer",
        error=empty_reply(),
    )


@pytest.fixture
async def rubric() -> AnalyticRubric:
    loaded = await load_analytic_rubric("B12", "refuse")
    assert loaded is not None
    return loaded


@pytest.mark.parametrize(
    "error",
    [empty_reply(), ProviderTimeoutError(provider="stub", endpoint="loopback", details="timed out")],
    ids=["empty-reply", "timeout"],
)
async def test_conversation_stops_at_the_turn_the_agent_did_not_answer(
    rubric: AnalyticRubric, error: ProviderError
) -> None:
    provider = ScriptedProvider([answered("first answer"), unanswered(error), answered("third")])
    inspection = ConversationInspection(rubric, extra_evidence=[])
    result = await inspection.execute(provider, CONFIG, FIXTURE)
    graded, unanswered_item, unsent_item = result.evidence
    assert len(provider.histories) == 2
    assert [item.step_number for item in result.evidence] == [1, 2, 3]
    assert graded.extraction_error is None and graded.passed
    for item in (unanswered_item, unsent_item):
        assert item.extraction_error == JudgeErrorKind.COMMUNICATION
        assert item.details["comm_failure"] is True
        assert item.details["unanswered_step"] == 2
    assert "not sent" not in unanswered_item.description
    assert "not sent" in unsent_item.description


async def test_step_count_is_what_a_step_by_step_failure_would_have_recorded(
    rubric: AnalyticRubric,
) -> None:
    """Run health measures an unreachable agent against the number of attempted probes, so
    stopping early must not shrink it: three steps planned, three items recorded."""
    provider = ScriptedProvider([unanswered(empty_reply())])
    result = await ConversationInspection(rubric, extra_evidence=[]).execute(
        provider, CONFIG, FIXTURE
    )
    assert len(provider.histories) == 1
    assert len(result.evidence) == len(THREE_STEP_PLAN.steps)
    assert all(item.details["comm_failure"] for item in result.evidence)


async def test_no_prompt_is_ever_sent_on_a_history_with_a_missing_reply(
    rubric: AnalyticRubric,
) -> None:
    provider = ScriptedProvider([unanswered(empty_reply()), answered("second"), answered("third")])
    await ConversationInspection(rubric, extra_evidence=[]).execute(provider, CONFIG, FIXTURE)
    for history in provider.histories:
        roles = [message.role for message in history if message.role != "system"]
        assert roles == ["user", "assistant"] * (len(roles) // 2) + ["user"]


async def test_answered_turns_before_the_gap_are_still_graded(rubric: AnalyticRubric) -> None:
    provider = ScriptedProvider([answered("one"), answered("two"), unanswered(empty_reply())])
    result = await ConversationInspection(rubric, extra_evidence=[]).execute(
        provider, CONFIG, FIXTURE
    )
    assert result.status == TestStatus.PASS
    assert result.score == 1.0
    assert result.error is None
    assert sum(1 for item in result.evidence if item.extraction_error is None) == 2


async def test_inspection_with_no_answered_prompt_is_inconclusive_despite_api_level_passes(
    rubric: AnalyticRubric,
) -> None:
    provider = ScriptedProvider([unanswered(empty_reply())])
    inspection = ConversationInspection(rubric, extra_evidence=[structural_pass()])
    result = await inspection.execute(provider, CONFIG, FIXTURE)
    assert result.status == TestStatus.INCONCLUSIVE
    assert result.insufficient_evidence is True
    assert result.passed is False


async def test_api_level_passes_still_count_once_the_agent_has_answered(
    rubric: AnalyticRubric,
) -> None:
    """Control for the test above: the same structural item, plus one answered prompt."""
    provider = ScriptedProvider([answered("one"), unanswered(empty_reply())])
    inspection = ConversationInspection(rubric, extra_evidence=[structural_pass()])
    result = await inspection.execute(provider, CONFIG, FIXTURE)
    assert result.status == TestStatus.PASS
    assert result.insufficient_evidence is False


def test_unanswered_prompts_alone_mean_no_answered_prompt() -> None:
    assert has_no_answered_prompt([unreachable("a"), unreachable("b"), structural_pass()])


def test_one_answered_prompt_is_enough() -> None:
    assert not has_no_answered_prompt([unreachable("a"), graded_reply(), structural_pass()])


def test_inspection_that_sends_no_prompt_is_not_affected() -> None:
    assert not has_no_answered_prompt([structural_pass(), structural_pass()])
    assert not has_no_answered_prompt([])


def test_reply_the_judge_could_not_grade_still_counts_as_answered() -> None:
    """The agent did reply; the judge failed. That is a grader problem, not a silent agent."""
    ungraded = graded_reply().model_copy(
        update={"passed": False, "extraction_error": JudgeErrorKind.CONTRACT}
    )
    assert not has_no_answered_prompt([unreachable("a"), ungraded])
