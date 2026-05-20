import logging
from typing import TYPE_CHECKING

from ifixai.evaluation.analytic_judge import (
    JudgeCommunicationError,
    JudgeContractError,
    JudgeExtractionError,
)
from ifixai.evaluation.errors import JudgePipelineRequiredError
from ifixai.evaluation.atomic_claims import (
    AtomicMode,
    AtomicScore,
    score_atomic_claims,
    score_atomic_claims_with_ground_truth,
)
from ifixai.evaluation.response_classifier import ResponseClass, classify_response
from ifixai.core.types import (
    AnalyticRubric,
    EvaluationCriteria,
    EvaluationMethod,
    EvaluationPipelineConfig,
    ExpectedClaim,
    JudgeErrorKind,
    PipelineResult,
    ReferenceResponse,
)

if TYPE_CHECKING:
    from ifixai.evaluation.analytic_judge import (
        AnalyticRubricJudge,
        EnsembleAnalyticRubricJudge,
    )

_logger = logging.getLogger(__name__)


class EvaluationPipeline:

    def __init__(
        self,
        config: EvaluationPipelineConfig,
        judge: "AnalyticRubricJudge | EnsembleAnalyticRubricJudge | None" = None,
    ) -> None:
        self._config = config
        self._judge = judge
        self._judge_calls_used = 0

    @property
    def judge_calls_used(self) -> int:
        return self._judge_calls_used

    async def evaluate(
        self,
        response: str,
        criteria: EvaluationCriteria,
        rubric: AnalyticRubric | None = None,
        references: list[ReferenceResponse] | None = None,
        context: str = "",
        context_vars: dict[str, str] | None = None,
    ) -> PipelineResult:
        if self._judge is None or rubric is None:
            missing = "judge" if self._judge is None else "rubric"
            raise JudgePipelineRequiredError(
                getattr(rubric, "test_id", "<unknown>"),
                f"{missing} not configured",
            )
        if self._judge is not None and rubric is not None:
            if (
                self._config.judge_max_calls > 0
                and self._judge_calls_used >= self._config.judge_max_calls
            ):
                _logger.warning(
                    "Judge budget exhausted (%d/%d calls used)",
                    self._judge_calls_used,
                    self._config.judge_max_calls,
                )
                return PipelineResult(
                    passed=False,
                    evaluation_result="inconclusive: judge budget exhausted",
                    evaluation_method=EvaluationMethod.JUDGE,
                )

            try:
                rubric_verdict = await self._judge.evaluate_with_rubric(
                    response, rubric, context, context_vars
                )
                self._judge_calls_used += 1
                return PipelineResult(
                    passed=rubric_verdict.passed,
                    evaluation_result=f"judge: {rubric_verdict.verdict} (weighted_score={rubric_verdict.weighted_score:.2f})",
                    evaluation_method=EvaluationMethod.JUDGE,
                    dimension_scores=rubric_verdict.dimension_scores,
                    rubric_verdict=rubric_verdict,
                )
            except JudgeCommunicationError as exc:
                _logger.exception("Judge communication error")
                self._judge_calls_used += 1
                return PipelineResult(
                    passed=False,
                    evaluation_result=f"extraction_error: communication: {exc}",
                    evaluation_method=EvaluationMethod.JUDGE,
                    extraction_error=JudgeErrorKind.COMMUNICATION,
                )
            except JudgeExtractionError as exc:
                _logger.error("Judge extraction error: %s", exc)
                self._judge_calls_used += 1
                return PipelineResult(
                    passed=False,
                    evaluation_result=f"extraction_error: extraction: {exc}",
                    evaluation_method=EvaluationMethod.JUDGE,
                    extraction_error=JudgeErrorKind.EXTRACTION,
                )
            except JudgeContractError as exc:
                _logger.error("Judge contract error: %s", exc)
                self._judge_calls_used += 1
                return PipelineResult(
                    passed=False,
                    evaluation_result=f"extraction_error: contract: {exc}",
                    evaluation_method=EvaluationMethod.JUDGE,
                    extraction_error=JudgeErrorKind.CONTRACT,
                )

        # Unreachable: the misconfig guard at the top of evaluate() raises
        # JudgePipelineRequiredError when judge or rubric is missing.
        raise JudgePipelineRequiredError(
            getattr(rubric, "test_id", "<unknown>"),
            "judge or rubric missing after pipeline entry",
        )

    async def classify(self, response: str, query: str) -> ResponseClass | None:
        if self._judge is None:
            return None
        if (
            self._config.judge_max_calls > 0
            and self._judge_calls_used >= self._config.judge_max_calls
        ):
            _logger.warning(
                "Judge budget exhausted (%d/%d calls used) — classify skipped",
                self._judge_calls_used,
                self._config.judge_max_calls,
            )
            return None
        self._judge_calls_used += 1
        try:
            provider, config = self._judge.classifier_provider()
        except AttributeError as exc:
            raise JudgePipelineRequiredError(
                "classify", f"classifier provider not accessible: {exc}"
            ) from exc
        try:
            return await classify_response(
                response_text=response,
                query=query,
                judge_provider=provider,
                judge_config=config,
            )
        except JudgeContractError as exc:
            _logger.error("Classifier contract violation (non-conforming output): %s", exc)
            return None

    async def evaluate_atomic(
        self,
        response: str,
        sources: str,
        mode: AtomicMode,
        expected_claims: list[ExpectedClaim] | None = None,
    ) -> AtomicScore | None:
        if self._judge is None:
            return None
        if (
            self._config.judge_max_calls > 0
            and self._judge_calls_used >= self._config.judge_max_calls
        ):
            _logger.warning(
                "Judge budget exhausted (%d/%d calls used) — atomic skipped",
                self._judge_calls_used,
                self._config.judge_max_calls,
            )
            return None
        self._judge_calls_used += 1
        try:
            judge_arg = self._judge.atomic_evaluator()
        except AttributeError as exc:
            raise JudgePipelineRequiredError(
                "atomic", f"evaluator not accessible: {exc}"
            ) from exc
        if expected_claims:
            return await score_atomic_claims_with_ground_truth(
                response=response,
                expected_claims=expected_claims,
                judge=judge_arg,
            )
        return await score_atomic_claims(
            response=response,
            sources=sources,
            mode=mode,
            judge=judge_arg,
        )
