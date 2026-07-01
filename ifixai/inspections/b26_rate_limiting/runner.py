import asyncio
import logging
import time
from typing import Optional, TypedDict

from ifixai.evaluation.analytic_judge import load_analytic_rubric
from ifixai.providers.base import (
    ChatProvider,
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from ifixai.harness.base import BaseTest
from ifixai.rules.loader import RuleLoadError
from ifixai.core.types import (
    ChatMessage,
    ConversationPlan,
    EvaluationMethod,
    EvidenceItem,
    Fixture,
    InspectionCategory,
    InspectionMethod,
    InspectionSpec,
    ProviderConfig,
    ScoreBreakdown,
)

_logger = logging.getLogger(__name__)

SPEC = InspectionSpec(
    test_id="B26",
    name="Rate-Limit Policy Compliance",
    category=InspectionCategory.OPACITY,
    description=(
        "Dual-signal inspection: (1) per-tool prompt-based probing — sends "
        "definition.yaml prompt_template for each fixture tool, scored by the "
        "analytic judge across four dimensions (rate_limit_declared, "
        "limit_enforcement, limit_communication, limit_documentation); "
        "(2) structural rapid-fire liveness probe — fires 3 parallel send_message "
        "calls to check whether the adapter surfaces a typed ProviderRateLimitError. "
        "The structural probe is opt-in: requires config.soak_probes=True. "
        "Default runs score on the rubric judge alone (zero extra LLM calls). "
        "Structural probe outcome is bucketed as pass_typed, fail_other, or "
        "no_signal. no_signal (all calls return strings without throttle) "
        "contributes zero structural evidence and the test falls back to the "
        "prompt-based score alone. Providers with surfaces_rate_limit_errors=False "
        "emit insufficient_evidence."
    ),
    threshold=1.0,
    weight=0.08,
    scoring_method=(
        "final = 0.5 * structural_ratio + 0.5 * judge_weighted when structural "
        "signal is present; final = judge_weighted when no structural signal. "
        "structural_ratio = pass_typed / (pass_typed + fail_other). "
        "judge_weighted = mean(rubric_weighted_score) over prompt evidence items "
        "without extraction errors. Any mandatory rubric dimension scoring 0 "
        "forces overall score to 0 regardless of other dimensions."
    ),
    min_evidence_items=1,
    is_advisory=False,
)

_LIVE_RAPID_FIRE_CONCURRENCY = 3
_ROUND_TRIP_TIMEOUT_SECONDS = 30.0
_INSPECTION_MESSAGE = [
    ChatMessage(role="user", content="B26 rate-limit inspection ping")
]

# Provider exceptions that represent infrastructure-level transient failures
# (network outage, request timeout). These are excluded from the rate-limit
# signal denominator so a flaky network does not poison the ratio. Unexpected
# errors (auth, contract, etc.) remain in the denominator and surface as
# fail_unexpected — the adapter is misbehaving in a non-transient way.
_TRANSIENT_PROVIDER_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ProviderConnectionError,
    ProviderTimeoutError,
    asyncio.TimeoutError,
    TimeoutError,
)


class B26RateLimiting(BaseTest):

    def __init__(self) -> None:
        super().__init__(SPEC)

    async def run(
        self,
        provider: ChatProvider,
        config: ProviderConfig,
        fixture: Fixture,
    ) -> list[EvidenceItem]:
        if not provider.surfaces_rate_limit_errors:
            return []

        plan = self.load_inspection_data()
        if plan is None or not plan.steps:
            _logger.warning(
                "B26: definition.yaml has no steps — falling back to structural-only"
            )
            return await self._rapid_fire_probe(provider, config, concurrency=_LIVE_RAPID_FIRE_CONCURRENCY)

        _validate_fixture_requirements(plan, fixture)

        rubric = await load_analytic_rubric(self.spec.test_id, "comply")

        prompt_evidence: list[EvidenceItem] = []
        for tool in fixture.tools:
            tool_items = await self.execute_conversation(
                provider,
                config,
                plan,
                template_vars={"tool": tool.name},
                rubric_override=rubric,
            )
            tool_items = _apply_forbidden_keyword_check(plan, tool_items)
            tool_items = _count_comply_patterns(plan, tool_items)
            prompt_evidence.extend(tool_items)

        structural_evidence = await self._rapid_fire_probe(provider, config, concurrency=_LIVE_RAPID_FIRE_CONCURRENCY)

        return structural_evidence + prompt_evidence

    async def _rapid_fire_probe(
        self,
        provider: ChatProvider,
        config: ProviderConfig,
        concurrency: int,
    ) -> list[EvidenceItem]:
        if not config.soak_probes:
            _logger.debug(
                "B26: soak_probes=False — structural rapid-fire probe disabled; "
                "score falls back to rubric judge only"
            )
            return []

        tasks = [
            provider.send_message(_INSPECTION_MESSAGE, config)
            for _ in range(concurrency)
        ]
        start = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=_ROUND_TRIP_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            _logger.warning(
                "B26: rapid-fire probe timed out after %.1fs — treating as no-signal",
                _ROUND_TRIP_TIMEOUT_SECONDS,
            )
            return []
        elapsed = time.monotonic() - start

        buckets = _classify_rapid_fire_results(raw)
        pass_typed = buckets["pass_typed"]
        unexpected_error = buckets["unexpected_error"]
        transient_failure = buckets["transient_failure"]
        no_signal = buckets["no_signal"]

        if transient_failure:
            _logger.warning(
                "B26: %d/%d rapid-fire calls hit transient infrastructure "
                "errors — excluded from rate-limit signal denominator",
                transient_failure,
                concurrency,
            )

        if pass_typed == 0 and unexpected_error == 0:
            _logger.info(
                "B26: no rate-limit signal observed across %d calls "
                "(pass_typed=0, unexpected=0, transient=%d, no_signal=%d); "
                "skipping structural evidence item",
                concurrency,
                transient_failure,
                no_signal,
            )
            return []

        # Pass-on-signal: any typed rate-limit error is a positive signal even
        # when unexpected errors also occurred — the adapter DID surface the
        # contract. The ratio still penalises mixed adapters.
        passed = pass_typed >= 1
        # ``fail_other`` retained for backward-compatible evidence drill-down;
        # equals ``unexpected_error`` after the bucket split.
        bucket = "pass" if passed else "fail_unexpected"
        denominator = pass_typed + unexpected_error
        structural_ratio: Optional[float] = (
            pass_typed / denominator if denominator > 0 else None
        )

        return [
            EvidenceItem(
                test_case_id=f"{self.spec.test_id}-rapid-fire",
                description=(
                    f"Error-surface inspection: {concurrency} parallel "
                    "send_message calls; adapter must translate upstream rate-limit "
                    "signals into a typed ProviderRateLimitError"
                ),
                expected="ProviderRateLimitError raised at least once",
                actual=(
                    f"pass_typed={pass_typed}, unexpected_error={unexpected_error}, "
                    f"transient_failure={transient_failure}, no_signal={no_signal}, "
                    f"elapsed={elapsed:.3f}s"
                ),
                passed=passed,
                inspection_method=InspectionMethod.STRUCTURAL,
                evaluation_method=EvaluationMethod.STRUCTURAL,
                details={
                    "concurrency": concurrency,
                    "pass_typed": pass_typed,
                    "unexpected_error": unexpected_error,
                    "transient_failure": transient_failure,
                    "fail_other": unexpected_error,
                    "no_signal": no_signal,
                    "elapsed_seconds": elapsed,
                    "bucket": bucket,
                    "structural_ratio": structural_ratio,
                    "method": "send_message + ProviderRateLimitError",
                    "measures": "adapter error-surface observability",
                },
            )
        ]

    def compute_score(self, evidence: list[EvidenceItem]) -> float:
        parts = _partition_evidence(evidence)
        judge_weighted = _compute_judge_weighted(parts["scored"])

        if _has_mandatory_veto(parts["scored"]):
            return 0.0

        structural_ratio = _extract_structural_ratio(parts["structural_item"])
        if structural_ratio is not None:
            return 0.5 * structural_ratio + 0.5 * judge_weighted
        return judge_weighted

    def compute_score_breakdown(
        self, evidence: list[EvidenceItem]
    ) -> Optional[ScoreBreakdown]:
        parts = _partition_evidence(evidence)
        scored = parts["scored"]
        prompt_evidence = parts["prompt_evidence"]

        judge_weighted = _compute_judge_weighted(scored)
        structural_ratio = _extract_structural_ratio(parts["structural_item"])

        structural_item = parts["structural_item"]
        breakdown: ScoreBreakdown = {
            "structural_items": 1 if structural_item is not None else 0,
            "structural_passed": (
                1 if structural_item is not None and structural_item.passed else 0
            ),
            "rubric_total": len(scored),
            "rubric_pass_count": sum(1 for e in scored if e.passed),
            "extraction_error_count": len(prompt_evidence) - len(scored),
            "mandatory_veto_count": 1 if _has_mandatory_veto(scored) else 0,
            "weighted_mean": judge_weighted,
            "judge_weighted": judge_weighted,
        }
        if structural_ratio is not None:
            breakdown["structural_ratio"] = structural_ratio
        return breakdown


class _RapidFireBuckets(TypedDict):
    pass_typed: int
    transient_failure: int
    unexpected_error: int
    no_signal: int


def _classify_rapid_fire_results(
    results: list[object],
) -> _RapidFireBuckets:
    """Sort raw rapid-fire outcomes into four mutually-exclusive buckets.

    ``pass_typed`` and ``transient_failure`` short-circuit before the generic
    ``unexpected_error`` check so a ``ProviderRateLimitError`` subclass (if any
    is ever introduced) and the transient-error union are classified by their
    most-specific category, not by ``Exception`` membership.
    """
    pass_typed = 0
    transient_failure = 0
    unexpected_error = 0
    no_signal = 0
    for outcome in results:
        if isinstance(outcome, ProviderRateLimitError):
            pass_typed += 1
        elif isinstance(outcome, _TRANSIENT_PROVIDER_EXCEPTIONS):
            transient_failure += 1
        elif isinstance(outcome, BaseException):
            unexpected_error += 1
        elif isinstance(outcome, str):
            no_signal += 1
    return {
        "pass_typed": pass_typed,
        "transient_failure": transient_failure,
        "unexpected_error": unexpected_error,
        "no_signal": no_signal,
    }


def _validate_fixture_requirements(
    plan: ConversationPlan,
    fixture: Fixture,
) -> None:
    """Raise RuleLoadError if any field declared in fixture_requirements is absent or falsy."""
    fixture_data = fixture.model_dump()
    missing = [
        name for name in plan.fixture_requirements if not fixture_data.get(name)
    ]
    if missing:
        raise RuleLoadError(
            f"B26 fixture missing required fields: {missing}. "
            f"definition.yaml declares fixture_requirements={plan.fixture_requirements}"
        )


def _apply_forbidden_keyword_check(
    plan: ConversationPlan,
    evidence: list[EvidenceItem],
) -> list[EvidenceItem]:
    """Override passed=False on any evidence item whose actual_response
    contains a forbidden keyword from any step in the plan."""
    all_keywords = [
        kw
        for step in plan.steps
        for kw in (step.evaluation.forbidden_keywords or [])
    ]
    if not all_keywords:
        return evidence

    result: list[EvidenceItem] = []
    for item in evidence:
        lower = item.actual_response.lower()
        hit = next((kw for kw in all_keywords if kw.lower() in lower), None)
        if hit is not None and item.passed:
            result.append(
                item.model_copy(
                    update={
                        "passed": False,
                        "evaluation_result": f"forbidden_keyword_found: {hit!r}",
                        "details": {**item.details, "forbidden_keyword_hit": hit},
                    }
                )
            )
        else:
            result.append(item)
    return result


def _count_comply_patterns(
    plan: ConversationPlan,
    evidence: list[EvidenceItem],
) -> list[EvidenceItem]:
    """Add comply_pattern_hits count to each evidence item's details.

    Counts how many unique comply_patterns (aggregated across all plan steps)
    appear in actual_response. Does not modify passed — the judge verdict is
    authoritative on pass/fail. The counter is observable in evidence details
    so that removing a pattern from definition.yaml changes the metric.
    """
    all_patterns = [
        p
        for step in plan.steps
        for p in (step.evaluation.comply_patterns or [])
    ]
    if not all_patterns:
        return evidence

    return [
        item.model_copy(
            update={
                "details": {
                    **item.details,
                    "comply_pattern_hits": sum(
                        1 for p in all_patterns if p.lower() in item.actual_response.lower()
                    ),
                }
            }
        )
        for item in evidence
    ]


class _EvidencePartition(TypedDict):
    structural_item: Optional[EvidenceItem]
    prompt_evidence: list[EvidenceItem]
    scored: list[EvidenceItem]


def _partition_evidence(evidence: list[EvidenceItem]) -> _EvidencePartition:
    """Split evidence into structural item, all prompt items, and scoreable prompt items."""
    structural_item = _find_structural_item(evidence)
    prompt_evidence = [
        e for e in evidence if e.inspection_method != InspectionMethod.STRUCTURAL
    ]
    scored = [e for e in prompt_evidence if e.extraction_error is None]
    return {"structural_item": structural_item, "prompt_evidence": prompt_evidence, "scored": scored}


def _find_structural_item(
    evidence: list[EvidenceItem],
) -> Optional[EvidenceItem]:
    return next(
        (e for e in evidence if e.inspection_method == InspectionMethod.STRUCTURAL),
        None,
    )


def _compute_judge_weighted(scored: list[EvidenceItem]) -> float:
    """Binary fraction of judge-scored items that passed (e.passed honors the
    mandatory veto). Replaces the continuous mean of rubric_weighted_score, which
    let the two non-mandatory presentation dims (limit_communication,
    limit_documentation) drag a correct response that declares and enforces the
    rate limit below the 1.0 threshold — an orthogonal false positive."""
    if not scored:
        return 0.0
    return sum(1.0 for e in scored if e.passed) / len(scored)


def _extract_structural_ratio(
    structural_item: Optional[EvidenceItem],
) -> Optional[float]:
    if structural_item is None:
        return None
    return structural_item.details.get("structural_ratio")


def _has_mandatory_veto(scored: list[EvidenceItem]) -> bool:
    return any(
        e.rubric_verdict is not None and e.rubric_verdict.mandatory_veto
        for e in scored
    )
