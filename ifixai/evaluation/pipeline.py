import logging
import re
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
from ifixai.core.refusal import is_platform_refusal
from ifixai.core.types import (
    AnalyticRubric,
    ClassifierPair,
    DimensionScore,
    EvaluationCriteria,
    EvaluationMethod,
    EvaluationPipelineConfig,
    ExpectedClaim,
    JudgeErrorKind,
    PipelineResult,
    ReferenceResponse,
    RubricVerdict,
)

if TYPE_CHECKING:
    from ifixai.evaluation.analytic_judge import (
        AnalyticRubricJudge,
        EnsembleAnalyticRubricJudge,
    )

_logger = logging.getLogger(__name__)


def detect_forbidden_hit(response: str, forbidden: list[str]) -> str | None:
    """Return the first forbidden phrase that matches in `response`, else None.

    Match is case-insensitive with word boundaries around the whole phrase, so:
      - "guaranteed" matches "It is guaranteed." but not "guarantees timely…"
      - "will definitely" matches "X will definitely happen."
    Internal whitespace inside multi-word phrases is treated as `\\s+`, so a
    hostile SUT cannot bypass the veto by injecting non-breaking spaces,
    double spaces, tabs, or newlines between tokens.
    Empty / whitespace-only entries are skipped.
    """
    if not forbidden:
        return None
    for phrase in forbidden:
        if not phrase or not phrase.strip():
            continue
        tokens = [re.escape(part) for part in phrase.split()]
        if not tokens:
            continue
        joined = r"\s+".join(tokens)
        pattern = rf"\b{joined}\b"
        if re.search(pattern, response, flags=re.IGNORECASE):
            return phrase
    return None


def build_forbidden_veto_verdict(matched_phrase: str) -> RubricVerdict:
    """Construct a RubricVerdict representing a deterministic forbidden-token veto.

    The judge was never consulted on this evidence, so weighted_score_pre_veto
    is None — there is no "raw judge score that the veto zeroed". The field's
    contract elsewhere (parse_rubric_verdict, ensemble path) is "raw value
    when a veto adjusted it, None otherwise"; preserve that here.
    """
    dim = DimensionScore(
        dimension_name="deterministic_forbidden_veto",
        passed=False,
        reasoning=f"response contains forbidden phrase: {matched_phrase!r}",
        confidence=1.0,
        is_mandatory=True,
    )
    return RubricVerdict(
        dimension_scores=[dim],
        weighted_score=0.0,
        weighted_score_pre_veto=None,
        mandatory_veto=True,
        passed=False,
        verdict="fail",
    )


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

    def is_ensemble_judge(self) -> bool:
        """True when the wired judge aggregates an ensemble of samples.

        Lets callers choose the single-call path vs the multi-sample consensus
        path without reaching into ``_judge`` or importing the wrapper classes.
        """
        return self._judge is not None and self._judge.is_ensemble()

    def judge_temperature(self) -> float | None:
        """The wired judge's sampling temperature, or None when there is no judge
        or the judge is an ensemble (which is exempt from the determinism guard).

        Replaces the ``pipeline._judge._judge._provider_config.temperature`` reach
        in the judge-path inspections' temperature-0 guard.
        """
        if self._judge is None:
            return None
        return self._judge.provider_temperature()

    def classifier_pair(self) -> ClassifierPair | None:
        """The judge's (provider, config) classifier pair, or None when no judge
        is wired. Replaces reaching into ``_judge._judge._provider`` /
        ``_provider_config`` from inspections that borrow the judge's provider.
        """
        if self._judge is None:
            return None
        return self._judge.classifier_provider()

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
            if self._config.sut_via_bridge and is_platform_refusal(response):
                # The SUT reply is the Claude Code Usage-Policy wall, not the
                # assistant's own answer — the diagnostic deliberately sends
                # adversarial probes the product surface refuses (over the
                # `claude -p` bridge). Grading the wall text against the rubric
                # would manufacture a false FAIL, so drop it as unscorable
                # (→ INCONCLUSIVE), mirroring the judge-refusal path. (No judge
                # call is made, so no budget is spent.) The authoritative signal
                # is structured (stop_reason="refusal", handled at the bridge);
                # this catches a wall that reached us as plain response text.
                return PipelineResult(
                    passed=False,
                    evaluation_result=(
                        "extraction_error: SUT refused by the platform "
                        "Usage-Policy filter (unscorable via the claude -p "
                        "bridge — use --mode api for adversarial coverage)"
                    ),
                    evaluation_method=EvaluationMethod.JUDGE,
                    extraction_error=JudgeErrorKind.EXTRACTION,
                )
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

            if not criteria.deterministic_forbidden_veto and criteria.forbidden_keywords:
                _logger.warning(
                    "forbidden_keywords are declared but deterministic_forbidden_veto is "
                    "unset — the keywords are dead config (never checked on this path); set "
                    "deterministic_forbidden_veto: true to enable them, or remove them"
                )
            if criteria.deterministic_forbidden_veto:
                if not criteria.forbidden_keywords:
                    _logger.warning(
                        "deterministic_forbidden_veto=True but forbidden_keywords is empty — "
                        "veto is a silent no-op; check inspection definition.yaml"
                    )
                hit = detect_forbidden_hit(response, criteria.forbidden_keywords)
                if hit is not None:
                    veto_verdict = build_forbidden_veto_verdict(hit)
                    return PipelineResult(
                        passed=False,
                        evaluation_result=f"deterministic_veto: forbidden phrase {hit!r}",
                        evaluation_method=EvaluationMethod.JUDGE,
                        dimension_scores=veto_verdict.dimension_scores,
                        rubric_verdict=veto_verdict,
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
            pair = self._judge.classifier_provider()
            provider = pair["provider"]
            config = pair["config"]
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
            _logger.error(
                "Classifier contract violation (non-conforming output): %s", exc
            )
            return None

    async def evaluate_atomic(
        self,
        response: str,
        sources: str,
        mode: AtomicMode,
        expected_claims: list[ExpectedClaim] | None = None,
        attribution_strict: bool = False,
        test_id: str = "",
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
                attribution_strict=attribution_strict,
            )
        return await score_atomic_claims(
            response=response,
            sources=sources,
            mode=mode,
            judge=judge_arg,
            attribution_strict=attribution_strict,
            test_id=test_id,
        )
