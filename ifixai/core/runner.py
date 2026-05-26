import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from ifixai.core.concurrency import ConcurrencyGovernor
from ifixai.evaluation.analytic_judge import (
    AnalyticRubricJudge,
    EnsembleAnalyticRubricJudge,
)
from ifixai.evaluation.pipeline import EvaluationPipeline
from ifixai.providers.base import ChatProvider, detect_capabilities
from ifixai.reporting.gap_analysis import identify_gaps
from ifixai.reporting.grading import score_to_grade
from ifixai.reporting.scorecard import (
    advisory_inspection_warnings,
    attestation_inspection_warnings,
    b22_determinism_warning,
    b32_not_applicable_warning,
    exploratory_inspection_warnings,
    extraction_error_warnings,
    insufficient_evidence_warnings,
    scorecard_warnings,
)
from ifixai.scoring.category_weights import (
    DEFAULT_CATEGORY_WEIGHTS,
    STRATEGIC_TEST_IDS,
)
from ifixai.scoring.engine import (
    compute_category_score,
    compute_overall_score,
    compute_strategic_score,
)
from ifixai.harness.registry import ALL_SPECS, INSPECTION_REGISTRY
from ifixai.scoring.mandatory_minimums import (
    PASS_THRESHOLD,
    SCORE_CAP_ON_FAILURE,
    apply_consistency_cap,
    cap_score_if_minimums_failed,
    check_mandatory_minimums,
)
from ifixai.harness.consistency import CrossHookValidator
from ifixai.judge.config import JudgeConfig
from ifixai.judge.evaluator import EnsembleJudgeEvaluator, JudgeEvaluator
from ifixai.core.types import (
    InspectionCategory,
    TestResult,
    TestStatus,
    InspectionSpec,
    EvaluationPipelineConfig,
    Fixture,
    ProviderCapabilities,
    ProviderConfig,
    TestRunResult,
)

ProgressCallback = Callable[[str, int, int, "TestResult"], None]


class SelfJudgeNotAllowedError(Exception):
    pass


SPECIFICATION_VERSION = "3.0"

_logger = logging.getLogger(__name__)

_EXPECTED_INSPECTION_ERRORS: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    NotImplementedError,
    RuntimeError,
)


async def _aclose_judge(
    judge: "JudgeEvaluator | EnsembleJudgeEvaluator | None",
) -> None:
    """Best-effort teardown for the judge's underlying HTTP/SDK pool.

    Swallows close-time errors so a teardown failure cannot mask the
    original exception that the inspection pipeline is propagating.
    """
    if judge is None:
        return
    try:
        await judge.aclose()
    except Exception:
        _logger.exception("Judge teardown failed during aclose")


async def run_all(
    provider: ChatProvider,
    config: ProviderConfig,
    fixture: Fixture,
    system_name: str = "",
    system_version: str = "1.0",
    progress_callback: Optional[ProgressCallback] = None,
    judge_config: JudgeConfig | None = None,
    pipeline_config: EvaluationPipelineConfig | None = None,
    governor: ConcurrencyGovernor | None = None,
    capabilities: ProviderCapabilities | None = None,
) -> TestRunResult:

    if capabilities is None:
        capabilities = await detect_capabilities(provider, config)

    judge = _build_judge_evaluator(judge_config)

    pipeline = _build_pipeline(pipeline_config, judge)

    try:
        test_results = await _execute_inspections(
            inspections=INSPECTION_REGISTRY,
            specs=ALL_SPECS,
            provider=provider,
            config=config,
            fixture=fixture,
            capabilities=capabilities,
            progress_callback=progress_callback,
            pipeline_config=pipeline_config,
            pipeline=pipeline,
            governor=governor,
        )

        try:
            violations = await CrossHookValidator().run(provider, config)
        except Exception:
            _logger.exception("CrossHookValidator failed; skipping consistency checks")
            violations = []

        if violations:
            test_results, consistency_capped = apply_consistency_cap(test_results, violations)
            consistency_warnings = [v.detail for v in violations]
        else:
            consistency_capped = False
            consistency_warnings = []

        result = _build_result(
            test_results=test_results,
            system_name=system_name or config.provider,
            system_version=system_version,
            fixture_name=fixture.metadata.name,
            provider_name=config.provider,
            run_mode="full",
            judge_stats=judge.get_stats() if judge else None,
            provider_capabilities=capabilities,
            warnings=scorecard_warnings(judge_config, config.provider),
            consistency_warnings=consistency_warnings,
            consistency_capped=consistency_capped,
            sut_temperature=config.temperature,
            sut_seed=config.seed,
        )
        return result
    finally:
        await _aclose_judge(judge)


async def run_strategic(
    provider: ChatProvider,
    config: ProviderConfig,
    fixture: Fixture,
    system_name: str = "",
    system_version: str = "1.0",
    progress_callback: Optional[ProgressCallback] = None,
    judge_config: JudgeConfig | None = None,
    pipeline_config: EvaluationPipelineConfig | None = None,
    governor: ConcurrencyGovernor | None = None,
    capabilities: ProviderCapabilities | None = None,
) -> TestRunResult:

    if capabilities is None:
        capabilities = await detect_capabilities(provider, config)

    judge = _build_judge_evaluator(judge_config)

    pipeline = _build_pipeline(pipeline_config, judge)

    strategic_inspections = {
        bid: inspection
        for bid, inspection in INSPECTION_REGISTRY.items()
        if bid in STRATEGIC_TEST_IDS
    }
    strategic_specs = [s for s in ALL_SPECS if s.test_id in STRATEGIC_TEST_IDS]

    try:
        test_results = await _execute_inspections(
            inspections=strategic_inspections,
            specs=strategic_specs,
            provider=provider,
            config=config,
            fixture=fixture,
            capabilities=capabilities,
            progress_callback=progress_callback,
            pipeline_config=pipeline_config,
            pipeline=pipeline,
            governor=governor,
        )

        try:
            violations = await CrossHookValidator().run(provider, config)
        except Exception:
            _logger.exception("CrossHookValidator failed; skipping consistency checks")
            violations = []

        if violations:
            test_results, consistency_capped = apply_consistency_cap(test_results, violations)
            consistency_warnings = [v.detail for v in violations]
        else:
            consistency_capped = False
            consistency_warnings = []

        result = _build_result(
            test_results=test_results,
            system_name=system_name or config.provider,
            system_version=system_version,
            fixture_name=fixture.metadata.name,
            provider_name=config.provider,
            run_mode="strategic",
            provider_capabilities=capabilities,
            warnings=scorecard_warnings(judge_config, config.provider),
            consistency_warnings=consistency_warnings,
            consistency_capped=consistency_capped,
            sut_temperature=config.temperature,
            sut_seed=config.seed,
        )
        return result
    finally:
        await _aclose_judge(judge)


async def run_selected(
    test_ids: set[str],
    provider: ChatProvider,
    config: ProviderConfig,
    fixture: Fixture,
    system_name: str = "",
    system_version: str = "1.0",
    progress_callback: Optional[ProgressCallback] = None,
    judge_config: JudgeConfig | None = None,
    pipeline_config: EvaluationPipelineConfig | None = None,
    governor: ConcurrencyGovernor | None = None,
    capabilities: ProviderCapabilities | None = None,
) -> TestRunResult:
    """Run an explicit subset of inspections by test id.

    Generalizes run_strategic: instead of the fixed STRATEGIC_TEST_IDS set,
    the caller supplies the ids to run. The aggregate overall_score is still
    computed via _build_result, so it may be capped when a mandatory-minimum
    inspection is absent from the selection. Per-test results are exact.
    """
    if capabilities is None:
        capabilities = await detect_capabilities(provider, config)

    judge = _build_judge_evaluator(judge_config)

    pipeline = _build_pipeline(pipeline_config, judge)

    selected_inspections = {
        bid: inspection
        for bid, inspection in INSPECTION_REGISTRY.items()
        if bid in test_ids
    }
    selected_specs = [s for s in ALL_SPECS if s.test_id in test_ids]

    try:
        test_results = await _execute_inspections(
            inspections=selected_inspections,
            specs=selected_specs,
            provider=provider,
            config=config,
            fixture=fixture,
            capabilities=capabilities,
            progress_callback=progress_callback,
            pipeline_config=pipeline_config,
            pipeline=pipeline,
            governor=governor,
        )

        try:
            violations = await CrossHookValidator().run(provider, config)
        except Exception:
            _logger.exception("CrossHookValidator failed; skipping consistency checks")
            violations = []

        if violations:
            test_results, consistency_capped = apply_consistency_cap(test_results, violations)
            consistency_warnings = [v.detail for v in violations]
        else:
            consistency_capped = False
            consistency_warnings = []

        result = _build_result(
            test_results=test_results,
            system_name=system_name or config.provider,
            system_version=system_version,
            fixture_name=fixture.metadata.name,
            provider_name=config.provider,
            run_mode="selected",
            provider_capabilities=capabilities,
            warnings=scorecard_warnings(judge_config, config.provider),
            consistency_warnings=consistency_warnings,
            consistency_capped=consistency_capped,
            sut_temperature=config.temperature,
            sut_seed=config.seed,
        )
        return result
    finally:
        await _aclose_judge(judge)


async def run_single(
    test_id: str,
    provider: ChatProvider,
    config: ProviderConfig,
    fixture: Fixture,
    judge_config: JudgeConfig | None = None,
    pipeline_config: EvaluationPipelineConfig | None = None,
    progress_callback: Optional[ProgressCallback] = None,
    capabilities: ProviderCapabilities | None = None,
) -> TestResult:

    if capabilities is None:
        capabilities = await detect_capabilities(provider, config)

    judge = _build_judge_evaluator(judge_config)

    pipeline = _build_pipeline(pipeline_config, judge)

    inspection = INSPECTION_REGISTRY.get(test_id)
    if inspection is None:
        raise ValueError(
            f"Unknown test: {test_id}. "
            f"Available: {sorted(INSPECTION_REGISTRY.keys())}"
        )
    try:
        return await inspection.execute(
            provider,
            config,
            fixture,
            capabilities,
            pipeline_config=pipeline_config,
            pipeline=pipeline,
        )
    finally:
        await _aclose_judge(judge)


async def _execute_inspections(
    inspections: dict[str, object],
    specs: list[InspectionSpec],
    provider: ChatProvider,
    config: ProviderConfig,
    fixture: Fixture,
    capabilities: ProviderCapabilities | None = None,
    progress_callback: Optional[ProgressCallback] = None,
    pipeline_config: EvaluationPipelineConfig | None = None,
    pipeline: EvaluationPipeline | None = None,
    governor: ConcurrencyGovernor | None = None,
) -> list[TestResult]:
    if governor is None or governor.is_sequential:
        return await _run_sequential(
            inspections,
            specs,
            provider,
            config,
            fixture,
            capabilities,
            progress_callback,
            pipeline_config,
            pipeline,
        )
    return await _run_parallel(
        inspections,
        specs,
        provider,
        config,
        fixture,
        capabilities,
        progress_callback,
        pipeline_config,
        pipeline,
        governor,
    )


async def _run_sequential(
    inspections: dict[str, object],
    specs: list[InspectionSpec],
    provider: ChatProvider,
    config: ProviderConfig,
    fixture: Fixture,
    capabilities: ProviderCapabilities | None,
    progress_callback: Optional[ProgressCallback],
    pipeline_config: EvaluationPipelineConfig | None,
    pipeline: EvaluationPipeline | None,
) -> list[TestResult]:
    results: list[TestResult] = []
    total = len(inspections)
    spec_map = {s.test_id: s for s in specs}

    for index, (test_id, inspection) in enumerate(inspections.items(), start=1):
        spec = spec_map.get(test_id)
        try:
            result = await inspection.execute(provider, config, fixture, capabilities, pipeline_config=pipeline_config, pipeline=pipeline)  # type: ignore[union-attr]
        except _EXPECTED_INSPECTION_ERRORS as exc:
            _logger.exception(
                "Inspection %s failed during sequential execution", test_id
            )
            result = TestResult(
                test_id=test_id,
                name=spec.name if spec else test_id,
                category=spec.category if spec else InspectionCategory.FABRICATION,
                score=0.0,
                threshold=spec.threshold if spec else 0.0,
                passed=False,
                passing=False,
                evidence=[],
                error=f"Runner-level error: {exc}",
                error_message=str(exc),
            )

        results.append(result)

        if progress_callback and callable(progress_callback):
            progress_callback(test_id, index, total, result)

    return results


async def _execute_single_inspection(
    test_id: str,
    inspection: object,
    spec: InspectionSpec | None,
    provider: ChatProvider,
    config: ProviderConfig,
    fixture: Fixture,
    capabilities: ProviderCapabilities | None,
    pipeline_config: EvaluationPipelineConfig | None,
    pipeline: EvaluationPipeline | None,
    governor: ConcurrencyGovernor,
) -> TestResult:
    async with governor.acquire():
        try:
            return await inspection.execute(  # type: ignore[union-attr]
                provider,
                config,
                fixture,
                capabilities,
                pipeline_config=pipeline_config,
                pipeline=pipeline,
            )
        except _EXPECTED_INSPECTION_ERRORS as exc:
            _logger.exception("Inspection %s failed during parallel execution", test_id)
            return TestResult(
                test_id=test_id,
                name=spec.name if spec else test_id,
                category=spec.category if spec else InspectionCategory.FABRICATION,
                score=0.0,
                threshold=spec.threshold if spec else 0.0,
                passed=False,
                passing=False,
                evidence=[],
                error=f"Runner-level error: {exc}",
                error_message=str(exc),
            )


async def _run_parallel(
    inspections: dict[str, object],
    specs: list[InspectionSpec],
    provider: ChatProvider,
    config: ProviderConfig,
    fixture: Fixture,
    capabilities: ProviderCapabilities | None,
    progress_callback: Optional[ProgressCallback],
    pipeline_config: EvaluationPipelineConfig | None,
    pipeline: EvaluationPipeline | None,
    governor: ConcurrencyGovernor,
) -> list[TestResult]:
    items = list(inspections.items())
    total = len(items)
    spec_map = {s.test_id: s for s in specs}
    tasks = [
        _execute_single_inspection(
            test_id,
            inspection,
            spec_map.get(test_id),
            provider,
            config,
            fixture,
            capabilities,
            pipeline_config,
            pipeline,
            governor,
        )
        for test_id, inspection in items
    ]
    results: list[TestResult] = []
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        if progress_callback and callable(progress_callback):
            progress_callback(result.test_id, len(results), total, result)
    results.sort(key=lambda r: r.test_id)
    return results


def _build_judge_evaluator(
    judge_config: JudgeConfig | None,
) -> JudgeEvaluator | EnsembleJudgeEvaluator | None:
    if judge_config is None:
        return None
    if judge_config.providers is not None:
        return EnsembleJudgeEvaluator(judge_config)
    return JudgeEvaluator(judge_config)


def _build_pipeline(
    pipeline_config: EvaluationPipelineConfig | None,
    judge: JudgeEvaluator | EnsembleJudgeEvaluator | None,
) -> EvaluationPipeline | None:
    if pipeline_config is None:
        return None

    analytic_judge: AnalyticRubricJudge | EnsembleAnalyticRubricJudge | None = None
    if isinstance(judge, EnsembleJudgeEvaluator):
        analytic_judge = EnsembleAnalyticRubricJudge(judge)
    elif isinstance(judge, JudgeEvaluator):
        analytic_judge = AnalyticRubricJudge(judge)

    return EvaluationPipeline(
        config=pipeline_config,
        judge=analytic_judge,
    )


def _build_result(
    test_results: list[TestResult],
    system_name: str,
    system_version: str,
    fixture_name: str,
    provider_name: str,
    run_mode: str,
    judge_stats: dict | None = None,
    provider_capabilities: ProviderCapabilities | None = None,
    warnings: list[str] | None = None,
    consistency_warnings: list[str] | None = None,
    consistency_capped: bool = False,
    sut_temperature: float = 0.0,
    sut_seed: int | None = None,
) -> TestRunResult:

    test_weights = {spec.test_id: spec.weight for spec in ALL_SPECS}

    category_scores = [
        compute_category_score(
            test_results, category, test_weights, DEFAULT_CATEGORY_WEIGHTS
        )
        for category in InspectionCategory
    ]

    raw_overall = compute_overall_score(category_scores, DEFAULT_CATEGORY_WEIGHTS)

    minimums_result = check_mandatory_minimums(test_results)
    minimums_passed = minimums_result["minimums_passed"]
    minimum_status = minimums_result["minimum_status"]
    overall_score = cap_score_if_minimums_failed(raw_overall, minimums_passed)
    overall_score_before_cap = (
        raw_overall if (not minimums_passed and raw_overall is not None) else None
    )
    cap_bound = (
        raw_overall is not None
        and not minimums_passed
        and raw_overall > SCORE_CAP_ON_FAILURE
    )

    grade = score_to_grade(overall_score if overall_score is not None else 0.0)
    strategic_score = compute_strategic_score(test_results, STRATEGIC_TEST_IDS)
    gaps = identify_gaps(test_results)

    violations = [
        bid for bid, status in minimum_status.items() if status == TestStatus.FAIL
    ]
    inconclusive_minimums = [
        bid
        for bid, status in minimum_status.items()
        if status == TestStatus.INCONCLUSIVE
    ]

    combined_warnings = list(warnings) if warnings else []
    seen: set[str] = set(combined_warnings)
    for msg in insufficient_evidence_warnings(test_results):
        if msg not in seen:
            seen.add(msg)
            combined_warnings.append(msg)
    for msg in exploratory_inspection_warnings(test_results):
        if msg not in seen:
            seen.add(msg)
            combined_warnings.append(msg)
    for msg in advisory_inspection_warnings(test_results):
        if msg not in seen:
            seen.add(msg)
            combined_warnings.append(msg)
    for msg in attestation_inspection_warnings(test_results):
        if msg not in seen:
            seen.add(msg)
            combined_warnings.append(msg)
    for msg in extraction_error_warnings(test_results):
        if msg not in seen:
            seen.add(msg)
            combined_warnings.append(msg)
    b22_msg = b22_determinism_warning(test_results, sut_temperature, sut_seed)
    if b22_msg is not None and b22_msg not in seen:
        combined_warnings.append(b22_msg)
    b32_msg = b32_not_applicable_warning(test_results)
    if b32_msg is not None and b32_msg not in seen:
        combined_warnings.append(b32_msg)

    is_passed = overall_score is not None and overall_score >= PASS_THRESHOLD

    return TestRunResult(
        system_name=system_name,
        system_version=system_version,
        provider=provider_name,
        fixture_name=fixture_name,
        timestamp=datetime.now(timezone.utc),
        evaluation_date=datetime.now(timezone.utc),
        specification_version=SPECIFICATION_VERSION,
        overall_score=overall_score,
        overall_score_before_cap=overall_score_before_cap,
        grade=grade,
        strategic_score=strategic_score,
        passed=is_passed,
        test_results=test_results,
        category_scores=category_scores,
        mandatory_minimum_status=minimum_status,
        mandatory_minimums_passed=minimums_passed,
        mandatory_minimums_inconclusive=inconclusive_minimums,
        mandatory_minimum_violations=violations,
        score_capped=cap_bound or consistency_capped,
        gaps=gaps,
        run_mode=run_mode,
        provider_capabilities=provider_capabilities,
        judge_stats=judge_stats,
        warnings=combined_warnings,
        validation_warnings=list(consistency_warnings) if consistency_warnings else [],
    )


def _find_spec(test_id: str, specs: list[InspectionSpec]) -> InspectionSpec | None:
    for spec in specs:
        if spec.test_id == test_id:
            return spec
    return None
