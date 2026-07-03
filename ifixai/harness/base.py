import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Optional, TypeVar

import logging

from ifixai.evaluation.analytic_judge import load_analytic_rubric
from ifixai.evaluation.errors import JudgePipelineRequiredError
from ifixai.providers.base import ProviderEmptyContentError, ProviderError
from ifixai.providers.base import ChatProvider
from ifixai.utils.template_renderer import render
from ifixai.rules.loader import load_inspection_definition
from ifixai.scoring.engine import compute_test_ci

if TYPE_CHECKING:
    from ifixai.evaluation.pipeline import EvaluationPipeline

from ifixai.core.types import (
    AnalyticRubric,
    ScoreBreakdown,
    TestResult,
    TestStatus,
    InspectionSpec,
    ChatMessage,
    ConversationPlan,
    EvaluationMode,
    EvaluationPipelineConfig,
    EvidenceItem,
    Fixture,
    JudgeErrorKind,
    ProviderCapabilities,
    ProviderConfig,
)

_logger = logging.getLogger(__name__)


def build_system_message(
    system_prompt: str | None, run_nonce: str | None
) -> str | None:
    """Return the system content to send to the SUT, with run_nonce appended.

    The run nonce defeats deterministic prompt caching by a hostile provider:
    even with temperature=0 and a fixed seed, the system prompt bytes differ
    across runs because the nonce is random per run. The marker is placed on
    its own line at the end so it does not interfere with prompt semantics.
    Returns None when neither a system prompt nor a nonce is configured.
    """
    if not system_prompt and not run_nonce:
        return None
    if not run_nonce:
        return system_prompt
    marker = f"[run_id: {run_nonce}]"
    if not system_prompt:
        return marker
    return f"{system_prompt}\n{marker}"


class BaseTest(ABC):

    def __init__(self, spec: InspectionSpec) -> None:
        self.spec = spec
        self.capabilities = ProviderCapabilities()
        self.pipeline_config: EvaluationPipelineConfig | None = None
        self._pipeline: "EvaluationPipeline | None" = None
        self._fixture: Fixture | None = None

    @abstractmethod
    async def run(
        self,
        provider: ChatProvider,
        config: ProviderConfig,
        fixture: Fixture,
    ) -> list[EvidenceItem]:
        pass

    def compute_score_breakdown(
        self, evidence: list[EvidenceItem]
    ) -> Optional[ScoreBreakdown]:
        return None

    def get_variant_seed(self) -> Optional[int]:
        return None

    def get_variant_seed_pinned(self) -> bool:
        return False

    async def execute(
        self,
        provider: ChatProvider,
        config: ProviderConfig,
        fixture: Fixture,
        capabilities: ProviderCapabilities | None = None,
        pipeline_config: EvaluationPipelineConfig | None = None,
        pipeline: "EvaluationPipeline | None" = None,
    ) -> TestResult:
        self.capabilities = capabilities or ProviderCapabilities()
        self.pipeline_config = pipeline_config
        self._pipeline = pipeline
        self._fixture = fixture
        start = time.monotonic()
        try:
            evidence = await self.run(provider, config, fixture)
            score = self.compute_score(evidence)
            duration = time.monotonic() - start

            ci = None
            eval_mode = None
            if pipeline_config is not None:
                eval_mode = pipeline_config.mode
                if pipeline_config.mode == EvaluationMode.FULL:
                    ci = compute_test_ci(
                        evidence,
                        pipeline_config.ci_confidence_level,
                    )

            if self.spec.count_extraction_errors_as_fail:
                scoreable_count = len(evidence)
            else:
                scoreable_count = sum(1 for e in evidence if e.extraction_error is None)
            insufficient = scoreable_count < self.spec.min_evidence_items
            meets_threshold = score >= self.spec.threshold
            if insufficient:
                status = TestStatus.INCONCLUSIVE
            elif meets_threshold:
                status = TestStatus.PASS
            else:
                status = TestStatus.FAIL

            return TestResult(
                test_id=self.spec.test_id,
                spec=self.spec,
                name=self.spec.name,
                category=self.spec.category,
                score=score,
                threshold=self.spec.threshold,
                passed=status == TestStatus.PASS,
                passing=status == TestStatus.PASS,
                status=status,
                evidence=evidence,
                duration_seconds=duration,
                duration_ms=duration * 1000,
                confidence_interval=ci,
                evaluation_mode=eval_mode,
                insufficient_evidence=insufficient,
                score_breakdown=self.compute_score_breakdown(evidence),
                variant_seed=self.get_variant_seed(),
                variant_seed_pinned=self.get_variant_seed_pinned(),
            )
        except ProviderEmptyContentError as exc:
            # Empty SUT response is unscorable, not misconfigured. The run
            # completed; the model produced no scoreable output. Map to
            # INCONCLUSIVE so it routes through the existing inconclusive
            # CLI/scorecard plumbing (excluded from denominator, surfaced
            # in the inconclusive summary) instead of the ERROR banner.
            duration = time.monotonic() - start
            return TestResult(
                test_id=self.spec.test_id,
                spec=self.spec,
                name=self.spec.name,
                category=self.spec.category,
                score=0.0,
                threshold=self.spec.threshold,
                passed=False,
                passing=False,
                status=TestStatus.INCONCLUSIVE,
                evidence=[],
                duration_seconds=duration,
                duration_ms=duration * 1000,
                error=str(exc),
                error_message=str(exc),
                insufficient_evidence=True,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            return TestResult(
                test_id=self.spec.test_id,
                spec=self.spec,
                name=self.spec.name,
                category=self.spec.category,
                score=0.0,
                threshold=self.spec.threshold,
                passed=False,
                passing=False,
                status=TestStatus.ERROR,
                evidence=[],
                duration_seconds=duration,
                duration_ms=duration * 1000,
                error=str(exc),
                error_message=str(exc),
            )

    def load_inspection_data(self) -> ConversationPlan | None:
        try:
            plan = load_inspection_definition(self.spec.test_id)
            if plan is None:
                _logger.debug(
                    "%s: no YAML inspection definition, using hardcoded logic",
                    self.spec.test_id,
                )
            return plan
        except Exception as exc:
            _logger.warning(
                "%s: failed to load YAML inspection: %s — using hardcoded logic",
                self.spec.test_id,
                exc,
            )
            return None

    def compute_score(self, evidence: list[EvidenceItem]) -> float:
        if self.spec.count_extraction_errors_as_fail:
            scored = evidence
        else:
            scored = [e for e in evidence if e.extraction_error is None]
        if not scored:
            return 0.0
        total = sum(
            (
                e.rubric_weighted_score
                if e.rubric_weighted_score is not None
                else (1.0 if e.passed else 0.0)
            )
            for e in scored
        )
        return total / len(scored)

    def _binary_score(self, evidence: list[EvidenceItem]) -> float:
        """Fraction of judge-scored items that passed, where e.passed honors the
        mandatory veto. Subclasses whose SPEC metric is a pass-rate override
        compute_score to call this, so non-mandatory presentation dims cannot drag
        a correct response below threshold. Judge extraction-errors are excluded
        (-> INCONCLUSIVE) unless the spec counts them as failures.
        """
        scored = (
            evidence
            if self.spec.count_extraction_errors_as_fail
            else [e for e in evidence if e.extraction_error is None]
        )
        if not scored:
            return 0.0
        return sum(1.0 for e in scored if e.passed) / len(scored)

    def _fixture_defaults(self) -> dict[str, str]:
        fixture = self._fixture
        if fixture is None:
            return {}
        data_source_names = (
            [s.name for s in fixture.data_sources] if fixture.data_sources else []
        )
        data_source_primary = (
            data_source_names[0] if data_source_names else "primary data store"
        )
        data_sources_joined = (
            ", ".join(data_source_names) if data_source_names else "(none configured)"
        )
        tool_name = fixture.tools[0].name if fixture.tools else "general query"
        role_name = (
            fixture.users[0].roles[0]
            if (fixture.users and fixture.users[0].roles)
            else "user"
        )
        domain = fixture.metadata.domain if fixture.metadata else ""
        policy_summary = "default governance policy"
        policy_dict = fixture.policies.model_dump(exclude_unset=True)
        parts: list[str] = []
        audit = policy_dict.get("audit_logging")
        if audit:
            parts.append(f"audit_logging={audit}")
        if "citation_required" in policy_dict:
            parts.append(f"citation_required={policy_dict['citation_required']}")
        if "confidence_threshold" in policy_dict:
            parts.append(f"confidence_threshold={policy_dict['confidence_threshold']}")
        if parts:
            policy_summary = "; ".join(parts)
        return {
            "data_source": data_source_primary,
            "data_sources": data_sources_joined,
            "tool": tool_name,
            "role": role_name,
            "domain": domain,
            "policy": policy_summary,
        }

    async def execute_conversation(
        self,
        provider: ChatProvider,
        config: ProviderConfig,
        plan: ConversationPlan,
        template_vars: dict[str, str],
        judge: object = None,
        pipeline: object = None,
        extra_context_vars: dict[str, str] | None = None,
        rubric_override: AnalyticRubric | None = None,
        rubric_overrides_by_step: dict[int, AnalyticRubric] | None = None,
    ) -> list[EvidenceItem]:
        evidence: list[EvidenceItem] = []
        history: list[ChatMessage] = []

        if pipeline is None:
            pipeline = self._pipeline

        system_content = build_system_message(config.system_prompt, config.run_nonce)
        if system_content is not None:
            history.append(ChatMessage(role="system", content=system_content))

        merged_vars = {**self._fixture_defaults(), **template_vars}
        case_label = template_vars.get("case_id") or template_vars.get(
            "role", "default"
        )
        for step in plan.steps:
            prompt = render(step.prompt_template, merged_vars)
            history.append(ChatMessage(role="user", content=prompt))

            try:
                response = await provider.send_message(history, config)
                history.append(ChatMessage(role="assistant", content=response))

                if not step.score:
                    # Setup-only step: drive the SUT (and history) without
                    # producing an EvidenceItem. Skips the pipeline contract
                    # so a missing-pipeline configuration here is not an error.
                    continue

                if pipeline is not None:
                    step_rubric = rubric_override
                    if rubric_overrides_by_step is not None:
                        step_rubric = rubric_overrides_by_step.get(
                            step.step_id, rubric_override
                        )
                    result = await self._evaluate_with_pipeline(
                        pipeline,
                        response,
                        step,
                        plan.test_id,
                        prompt=prompt,
                        extra_context_vars=extra_context_vars,
                        rubric_override=step_rubric,
                    )
                    evidence.append(
                        EvidenceItem(
                            test_case_id=f"{plan.test_id}_step{step.step_id}_{case_label}",
                            description=f"Step {step.step_id}: {step.evaluation.expected_outcome}",
                            prompt_sent=prompt,
                            expected=step.evaluation.expected_outcome,
                            expected_behavior=step.evaluation.expected_outcome,
                            actual=response[:500],
                            actual_response=response,
                            evaluation_result=result.evaluation_result,
                            passed=result.passed,
                            step_number=step.step_id,
                            details={
                                "template_vars": template_vars,
                                "step_id": step.step_id,
                            },
                            evaluation_method=result.evaluation_method,
                            judge_verdict=result.judge_verdict,
                            dimension_scores=result.dimension_scores,
                            rubric_verdict=result.rubric_verdict,
                            rubric_weighted_score=(
                                result.rubric_verdict.weighted_score
                                if result.rubric_verdict is not None
                                else None
                            ),
                            extraction_error=result.extraction_error,
                        )
                    )
                else:
                    raise JudgePipelineRequiredError(
                        plan.test_id,
                        "execute_conversation requires an analytic judge pipeline",
                    )
            except (JudgePipelineRequiredError, ProviderEmptyContentError):
                # Both propagate to BaseTest.execute: JudgePipelineRequiredError
                # → ERROR (misconfig), ProviderEmptyContentError → INCONCLUSIVE
                # (unscorable). Do not swallow into a per-step "error" evidence
                # item or the outer status mapping is lost.
                raise
            except ProviderError as exc:
                # A transport/provider failure (a bad model id 404ing on every
                # call, an auth/connection error) is NOT a behavioural failure —
                # grading the empty reply would manufacture a false FAIL. Stamp
                # extraction_error so the scoring layer drops it as unscorable
                # (→ INCONCLUSIVE), mirroring b27's per-call handling. This is
                # also the honest signal the run-health gate reads to flag a dead
                # SUT as a measurement failure instead of a real grade.
                evidence.append(
                    EvidenceItem(
                        test_case_id=f"{plan.test_id}_step{step.step_id}_{case_label}",
                        description=f"Step {step.step_id}: provider error (unreachable)",
                        prompt_sent=prompt,
                        expected=step.evaluation.expected_outcome,
                        expected_behavior=step.evaluation.expected_outcome,
                        actual=f"error: {exc}",
                        actual_response=f"error: {exc}",
                        evaluation_result="error",
                        passed=False,
                        extraction_error=JudgeErrorKind.COMMUNICATION,
                        step_number=step.step_id,
                        details={"error": str(exc), "comm_failure": True},
                    )
                )
            except Exception as exc:
                evidence.append(
                    EvidenceItem(
                        test_case_id=f"{plan.test_id}_step{step.step_id}_{case_label}",
                        description=f"Step {step.step_id}: error",
                        prompt_sent=prompt,
                        expected=step.evaluation.expected_outcome,
                        expected_behavior=step.evaluation.expected_outcome,
                        actual=f"error: {exc}",
                        actual_response=f"error: {exc}",
                        evaluation_result="error",
                        passed=False,
                        step_number=step.step_id,
                        details={"error": str(exc)},
                    )
                )

        return evidence

    async def _evaluate_with_pipeline(
        self,
        pipeline: object,
        response: str,
        step: object,
        test_id: str,
        prompt: str = "",
        extra_context_vars: dict[str, str] | None = None,
        rubric_override: AnalyticRubric | None = None,
    ) -> object:
        if rubric_override is not None:
            rubric = rubric_override
        else:
            rubric = await load_analytic_rubric(
                test_id, step.evaluation.expected_outcome
            )
        context = f"Test {test_id}"

        context_vars: dict[str, str] = {"actual_response": response}
        if prompt:
            context_vars["inspection_input"] = prompt
        if extra_context_vars:
            context_vars.update(extra_context_vars)

        return await pipeline.evaluate(
            response=response,
            criteria=step.evaluation,
            rubric=rubric,
            references=None,
            context=context,
            context_vars=context_vars,
        )


async def send_single_turn(
    provider: ChatProvider,
    config: ProviderConfig,
    prompt: str,
) -> str:
    history: list[ChatMessage] = []
    system_content = build_system_message(config.system_prompt, config.run_nonce)
    if system_content is not None:
        history.append(ChatMessage(role="system", content=system_content))
    history.append(ChatMessage(role="user", content=prompt))
    return await provider.send_message(history, config)


_T = TypeVar("_T")


def sample_capped(
    items: Sequence[_T],
    max_n: int,
    seed: int,
    sort_key: Callable[[_T], object] | None = None,
) -> list[_T]:
    """Return items within the cap, else a deterministic seeded subsample.

    With sort_key, items are ordered by it first (so the same seed reproduces the
    same set AND order); without it, input order is preserved.
    """
    ordered = sorted(items, key=sort_key) if sort_key is not None else list(items)
    if len(ordered) <= max_n:
        return ordered
    chosen = random.Random(seed).sample(ordered, max_n)
    if sort_key is not None:
        chosen.sort(key=sort_key)
    return chosen
