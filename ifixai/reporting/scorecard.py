import json
from typing import Final

from ifixai.judge.config import JudgeConfig
from ifixai.mappings.loader import load_all_mappings
from ifixai.reporting.regulatory import (
    build_regulatory_json_section,
    build_regulatory_summary,
    get_test_regulatory_mappings,
)
from ifixai.core.types import TestResult, TestStatus, RegulatoryFramework, TestRunResult

SELF_JUDGE_BIAS_ADVISORY: Final[str] = (
    "self-judge bias: Standard-mode score not comparable to Full-mode "
    "(same provider judges its own output)"
)

INSUFFICIENT_EVIDENCE_PREFIX: Final[str] = "insufficient evidence: "
EXTRACTION_ERROR_PREFIX: Final[str] = "judge extraction failure: "
EXPLORATORY_INSPECTION_PREFIX: Final[str] = (
    "exploratory inspection (excluded from aggregation): "
)
ADVISORY_INSPECTION_PREFIX: Final[str] = (
    "advisory inspection (excluded from aggregation): "
)
ATTESTATION_INSPECTION_PREFIX: Final[str] = (
    "attestation inspection (deployer-attested, not scored): "
)
_PROMPT_DISPLAY_CAP: Final[int] = 2000
_ACTUAL_DISPLAY_CAP: Final[int] = 2000

B22_SKIPPED_MESSAGE: Final[str] = (
    "b22 skipped: SUT non-deterministic (pass --sut-temperature 0 or --sut-seed)"
)
SEED_UNSUPPORTED_PREFIX: Final[str] = "b12 seed accepted but provider cannot honour: "


def insufficient_evidence_warnings(
    test_results: list[TestResult],
) -> list[str]:
    messages: list[str] = []
    for br in test_results:
        if not br.insufficient_evidence:
            continue
        floor = br.spec.min_evidence_items if br.spec else 10
        messages.append(
            INSUFFICIENT_EVIDENCE_PREFIX
            + f"{br.test_id} (got {len(br.evidence)}, min {floor})"
        )
    return messages


def exploratory_inspection_warnings(
    test_results: list[TestResult],
) -> list[str]:
    messages: list[str] = []
    for br in test_results:
        if not br.spec or not br.spec.is_exploratory:
            continue
        messages.append(EXPLORATORY_INSPECTION_PREFIX + br.test_id)
    return messages


def advisory_inspection_warnings(
    test_results: list[TestResult],
) -> list[str]:
    messages: list[str] = []
    for br in test_results:
        if not br.spec or not br.spec.is_advisory:
            continue
        if not br.evidence:
            continue
        messages.append(ADVISORY_INSPECTION_PREFIX + br.test_id)
    return messages


def attestation_inspection_warnings(
    test_results: list[TestResult],
) -> list[str]:
    messages: list[str] = []
    for br in test_results:
        if not br.spec or not br.spec.is_attestation:
            continue
        messages.append(ATTESTATION_INSPECTION_PREFIX + br.test_id)
    return messages


def extraction_error_warnings(
    test_results: list[TestResult],
) -> list[str]:
    messages: list[str] = []
    for br in test_results:
        affected = sum(1 for ev in br.evidence if ev.extraction_error is not None)
        if affected == 0:
            continue
        messages.append(
            EXTRACTION_ERROR_PREFIX
            + f"{br.test_id} ({affected} evidence items affected)"
        )
    return messages


def b22_determinism_warning(
    test_results: list[TestResult],
    sut_temperature: float,
    sut_seed: int | None,
) -> str | None:
    if sut_temperature == 0.0 or sut_seed is not None:
        return None
    b22 = next(
        (br for br in test_results if br.test_id == "B22"),
        None,
    )
    if b22 is None or not b22.insufficient_evidence:
        return None
    return B22_SKIPPED_MESSAGE


B32_NOT_APPLICABLE_MESSAGE: Final[str] = (
    "b32 skipped: fixture.metadata.domain not set or generic — "
    "off-topic refusal is undefined for general-purpose assistants"
)


def b32_not_applicable_warning(
    test_results: list[TestResult],
) -> str | None:
    """Return a structured advisory when B32 was skipped for non-applicability.

    Mirrors b22_determinism_warning. B32 returns zero evidence when the fixture
    has no specific domain restriction; BaseTest.execute routes that to
    INCONCLUSIVE / insufficient_evidence. The aggregator already excludes the
    result; this helper surfaces the reason in the scorecard warnings strip.
    """
    b32 = next(
        (br for br in test_results if br.test_id == "B32"),
        None,
    )
    if b32 is None or not b32.insufficient_evidence:
        return None
    return B32_NOT_APPLICABLE_MESSAGE


def self_judge_bias_applies(
    judge_config: JudgeConfig | None,
    model_provider: str,
) -> bool:
    if judge_config is None:
        return True
    if not judge_config.providers:
        return True
    distinct = {p.provider for p in judge_config.providers}
    return not (len(distinct) >= 2 and model_provider not in distinct)


def scorecard_warnings(
    judge_config: JudgeConfig | None,
    model_provider: str,
    extra: list[str] | None = None,
) -> list[str]:
    warnings: list[str] = list(extra) if extra else []
    if self_judge_bias_applies(judge_config, model_provider):
        if SELF_JUDGE_BIAS_ADVISORY not in warnings:
            warnings.append(SELF_JUDGE_BIAS_ADVISORY)
    return warnings


def generate_json_report(result: TestRunResult) -> str:
    frameworks = load_all_mappings()

    report = {
        "metadata": build_metadata_section(result, frameworks),
        "overall": build_overall_section(result),
        "warnings": list(result.warnings),
        "validation_warnings": list(result.validation_warnings),
        "sensitivity_note": (
            "Differences < 0.15 between two scores are not statistically "
            "distinguishable at typical sample sizes. Always compare "
            "ci_lower/ci_upper bounds across inspections rather than point scores."
        ),
        "category_scores": build_category_scores_section(result),
        "mandatory_minimums": build_mandatory_minimums_section(result),
        "test_results": build_test_results_section(result, frameworks),
        "regulatory": build_regulatory_json_section(result, frameworks),
    }

    return json.dumps(report, indent=2, ensure_ascii=False)


def generate_markdown_report(result: TestRunResult) -> str:
    frameworks = load_all_mappings()

    sections = [
        render_header(result),
        render_summary(result),
        render_category_table(result),
        render_mandatory_minimums(result),
        render_consistency_warnings(result),
        render_test_table(result),
        render_advisory_section(result),
        render_exploratory_section(result),
        render_attestation_section(result),
        render_regulatory_compliance(result, frameworks),
        render_evidence_appendix(result),
        render_footer(result),
    ]

    return "\n\n".join(s for s in sections if s) + "\n"


def build_metadata_section(
    result: TestRunResult,
    frameworks: dict[str, RegulatoryFramework] | None = None,
) -> dict[str, object]:
    meta: dict[str, object] = {
        "system_name": result.system_name,
        "system_version": result.system_version,
        "provider": result.provider,
        "fixture_name": result.fixture_name,
        "evaluation_date": result.evaluation_date.isoformat(),
        "specification_version": result.specification_version,
        "run_mode": result.run_mode,
        "self_judged": result.self_judged,
    }
    if result.judge_relation:
        meta["judge_relation"] = result.judge_relation
    if result.judge_stats is not None:
        meta["judge_stats"] = result.judge_stats
    if frameworks:
        meta["regulatory_frameworks"] = [
            {"name": fw.framework, "version": fw.version} for fw in frameworks.values()
        ]
    return meta


def build_overall_section(result: TestRunResult) -> dict[str, object]:
    overall = result.overall_score
    section: dict[str, object] = {
        "score": None if overall is None else round(overall, 4),
        "score_pct": "n/a" if overall is None else f"{overall:.1%}",
        "grade": result.grade.value,
        "strategic_score": round(result.strategic_score, 4),
        "strategic_score_pct": f"{result.strategic_score:.1%}",
        "passed": result.passed,
        "verdict": _format_run_verdict(result).lower(),
        "mandatory_minimums_passed": result.mandatory_minimums_passed,
        "mandatory_minimums_inconclusive": list(result.mandatory_minimums_inconclusive),
    }
    if result.overall_score_before_cap is not None:
        section["score_before_cap"] = round(result.overall_score_before_cap, 4)
        cap_bound = (
            result.overall_score is not None
            and result.overall_score_before_cap > result.overall_score
        )
        section["cap_applied"] = cap_bound
    return section


def build_category_scores_section(
    result: TestRunResult,
) -> list[dict[str, object]]:
    return [
        {
            "category": cs.category.value,
            "score": None if cs.score is None else round(cs.score, 4),
            "score_pct": "n/a" if cs.score is None else f"{cs.score:.1%}",
            "weight": cs.weight,
            "test_count": len(cs.test_ids),
            "test_ids": cs.test_ids,
        }
        for cs in result.category_scores
    ]


def build_mandatory_minimums_section(
    result: TestRunResult,
) -> dict[str, object]:
    return {
        "all_passed": result.mandatory_minimums_passed,
        "any_inconclusive": bool(result.mandatory_minimums_inconclusive),
        "per_test": {
            test_id: status.value
            for test_id, status in result.mandatory_minimum_status.items()
        },
        "violations": list(result.mandatory_minimum_violations),
        "inconclusive": list(result.mandatory_minimums_inconclusive),
    }


def build_test_results_section(
    result: TestRunResult,
    frameworks: dict[str, RegulatoryFramework] | None = None,
) -> list[dict[str, object]]:
    items = []
    for br in result.test_results:
        evidence_list = []
        for ev in br.evidence:
            ev_dict: dict[str, object] = {
                "test_case_id": ev.test_case_id,
                "description": ev.description,
                "prompt_sent": ev.prompt_sent,
                "expected": ev.expected or ev.expected_behavior,
                "actual": ev.actual_response or ev.actual,
                "evaluation_result": ev.evaluation_result,
                "passed": ev.passed,
                "inspection_method": ev.inspection_method.value,
                "evaluation_method": ev.evaluation_method.value,
            }
            if ev.dimension_scores:
                ev_dict["dimension_scores"] = [
                    {
                        "dimension_name": ds.dimension_name,
                        "passed": ds.passed,
                        "reasoning": ds.reasoning,
                        "confidence": ds.confidence,
                        "is_mandatory": ds.is_mandatory,
                    }
                    for ds in ev.dimension_scores
                ]
            if ev.rubric_verdict:
                ev_dict["rubric_verdict"] = {
                    "weighted_score": ev.rubric_verdict.weighted_score,
                    "weighted_score_pre_veto": ev.rubric_verdict.weighted_score_pre_veto,
                    "mandatory_veto": ev.rubric_verdict.mandatory_veto,
                    "passed": ev.rubric_verdict.passed,
                    "verdict": ev.rubric_verdict.verdict,
                }
            evidence_list.append(ev_dict)

        is_inconclusive = br.status == TestStatus.INCONCLUSIVE
        is_error = br.status == TestStatus.ERROR
        unscored = is_inconclusive or is_error
        br_dict: dict[str, object] = {
            "test_id": br.test_id,
            "name": br.name,
            "category": br.category.value,
            "score": None if unscored else round(br.score, 4),
            "score_pct": "n/a" if unscored else f"{br.score:.1%}",
            "threshold": br.threshold,
            "passing": br.passing,
            "status": br.status.value,
            "evidence_count": len(br.evidence),
            "evidence": evidence_list,
            "duration_ms": round(br.duration_ms, 1),
            "error": br.error,
            "regulatory_mappings": get_test_regulatory_mappings(br.test_id, frameworks),
        }
        if is_error and br.error_message:
            br_dict["error_message"] = br.error_message
        br_dict["evaluation_path"] = _dominant_evaluation_path(br)
        if br.confidence_interval:
            br_dict["ci_lower"] = round(br.confidence_interval.lower, 4)
            br_dict["ci_upper"] = round(br.confidence_interval.upper, 4)
            br_dict["confidence_interval"] = {
                "lower": br.confidence_interval.lower,
                "upper": br.confidence_interval.upper,
                "method": br.confidence_interval.method,
                "sample_size": br.confidence_interval.sample_size,
                "warning": br.confidence_interval.warning,
            }
        if br.evaluation_mode:
            br_dict["evaluation_mode"] = br.evaluation_mode.value
        if br.judge_calls_used:
            br_dict["judge_calls_used"] = br.judge_calls_used
        items.append(br_dict)
    return items


def render_header(result: TestRunResult) -> str:
    eval_mode = "deterministic"
    for br in result.test_results:
        if br.evaluation_mode:
            eval_mode = br.evaluation_mode.value
            break

    header = (
        f"# ifixai Scorecard — {result.system_name}"
        f"{f' v{result.system_version}' if result.system_version else ''}\n\n"
        f"**Specification Version:** {result.specification_version}  \n"
        f"**Provider:** {result.provider}  \n"
        f"**Fixture:** {result.fixture_name}  \n"
        f"**Evaluation Date:** {result.evaluation_date.strftime('%Y-%m-%d %H:%M UTC')}  \n"
        f"**Run Mode:** {result.run_mode}  \n"
        f"**Evaluation Mode:** {eval_mode}"
    )
    return header


def _format_run_minimums_status(result: TestRunResult) -> str:
    statuses = result.mandatory_minimum_status.values()
    if any(s == TestStatus.FAIL for s in statuses):
        return "FAIL"
    if any(s == TestStatus.INCONCLUSIVE for s in statuses):
        return "INCONCLUSIVE"
    return "PASS"


def _format_run_verdict(result: TestRunResult) -> str:
    # ERROR outranks every other verdict: a misconfigured run cannot be
    # interpreted as PASS, FAIL, or INCONCLUSIVE — operators must re-run
    # with the configuration fixed before any verdict is trusted.
    if any(br.status == TestStatus.ERROR for br in result.test_results):
        return "ERROR"
    if result.passed:
        return "PASS"
    if result.mandatory_minimum_violations:
        return "FAIL"
    if result.overall_score is None or result.mandatory_minimums_inconclusive:
        return "INCONCLUSIVE"
    return "FAIL"


def render_summary(result: TestRunResult) -> str:
    minimums_status = _format_run_minimums_status(result)
    verdict = _format_run_verdict(result)
    overall_display = (
        "n/a (insufficient evidence)"
        if result.overall_score is None
        else f"{result.overall_score:.1%}"
    )

    return (
        f"## Overall Score\n\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| **Overall Score** | {overall_display} |\n"
        f"| **Grade** | {result.grade.value} |\n"
        f"| **Verdict** | {verdict} |\n"
        f"| **Strategic Score** | {result.strategic_score:.1%} |\n"
        f"| **Mandatory Minimums** | {minimums_status} |"
    )


def render_category_table(result: TestRunResult) -> str:
    lines = [
        "## Category Scores\n",
        "| Category | Score | Tests |",
        "|---|---|---|",
    ]

    for cs in result.category_scores:
        test_count = len(cs.test_ids)
        score_display = "n/a" if cs.score is None else f"{cs.score:.1%}"
        lines.append(f"| {cs.category.value} | {score_display} | {test_count} |")

    return "\n".join(lines)


def render_mandatory_minimums(result: TestRunResult) -> str:
    if not result.mandatory_minimum_status:
        return "## Mandatory Minimums\n\nNo mandatory minimums defined."

    lines = [
        "## Mandatory Minimums\n",
        "| Test | Status |",
        "|---|---|",
    ]

    for test_id, status_value in sorted(result.mandatory_minimum_status.items()):
        status = _STATUS_LABELS.get(status_value, _STATUS_LABELS[TestStatus.FAIL])
        lines.append(f"| {test_id} | {status} |")

    return "\n".join(lines)


def _is_advisory_result(br: TestResult) -> bool:
    return bool(br.spec and br.spec.is_advisory)


def _is_exploratory_result(br: TestResult) -> bool:
    return bool(br.spec and br.spec.is_exploratory)


def _is_attestation_result(br: TestResult) -> bool:
    return bool(br.spec and br.spec.is_attestation)


_STATUS_LABELS: Final[dict[TestStatus, str]] = {
    TestStatus.PASS: "PASS",
    TestStatus.FAIL: "**FAIL**",
    TestStatus.INCONCLUSIVE: "INCONCLUSIVE",
    TestStatus.ERROR: "**ERROR**",
}


def _format_test_status(br: TestResult) -> str:
    if br.error:
        return _STATUS_LABELS[TestStatus.ERROR]
    return _STATUS_LABELS.get(br.status, _STATUS_LABELS[TestStatus.FAIL])


def _format_method_mix(br: TestResult) -> str:
    if not br.evidence:
        return "—"
    counts: dict[str, int] = {}
    for ev in br.evidence:
        method = ev.evaluation_method.value
        counts[method] = counts.get(method, 0) + 1
    parts = [f"{n}× {m}" for m, n in sorted(counts.items())]
    return ", ".join(parts)


def _dominant_evaluation_path(br: TestResult) -> str:
    """Summarise the evaluation path(s) used for an inspection."""
    if not br.evidence:
        return "NONE"
    methods: set[str] = {ev.evaluation_method.value for ev in br.evidence}
    if len(methods) == 1:
        return next(iter(methods))
    return "MIXED:" + "+".join(sorted(methods))


def render_test_table(result: TestRunResult) -> str:
    scored = [
        br
        for br in result.test_results
        if not _is_advisory_result(br)
        and not _is_exploratory_result(br)
        and not _is_attestation_result(br)
    ]

    lines = [
        "## Test Results\n",
        "| ID | Name | Score | Threshold | Path | Method | Status |",
        "|---|---|---|---|---|---|---|",
    ]

    _UNSCORED_STATUSES = {TestStatus.INCONCLUSIVE, TestStatus.ERROR}
    for br in scored:
        status = _format_test_status(br)
        score_display = "n/a" if br.status in _UNSCORED_STATUSES else f"{br.score:.1%}"
        if br.confidence_interval and br.status not in _UNSCORED_STATUSES:
            score_display += f" [{br.confidence_interval.lower:.2f}, {br.confidence_interval.upper:.2f}]"
        eval_path = _dominant_evaluation_path(br)
        method_mix = _format_method_mix(br)
        lines.append(
            f"| {br.test_id} | {br.name} | {score_display} "
            f"| {br.threshold:.0%} | {eval_path} | {method_mix} | {status} |"
        )

    has_dimensions = any(
        ev.rubric_verdict is not None
        for br in result.test_results
        for ev in br.evidence
    )
    if has_dimensions:
        lines.append("")
        lines.append("### Dimension Breakdown\n")
        for br in result.test_results:
            for ev in br.evidence:
                if ev.rubric_verdict and ev.dimension_scores:
                    lines.append(f"**{br.test_id}** — {ev.description}\n")
                    for ds in ev.dimension_scores:
                        status_icon = "PASS" if ds.passed else "**FAIL**"
                        mandatory_tag = " (mandatory)" if ds.is_mandatory else ""
                        lines.append(
                            f"- [{status_icon}] {ds.dimension_name}{mandatory_tag}: {ds.reasoning}"
                        )
                    rv = ev.rubric_verdict
                    veto_note = " | Mandatory veto: YES" if rv.mandatory_veto else ""
                    lines.append(
                        f"- Weighted score: {rv.weighted_score:.2f} | Verdict: {rv.verdict}{veto_note}\n"
                    )

    results_with_extras = [
        br
        for br in result.test_results
        if br.score_breakdown is not None or br.variant_seed is not None
    ]
    if results_with_extras:
        lines.append("")
        lines.append("### Score Breakdown\n")
        for br in results_with_extras:
            if br.score_breakdown is not None:
                bd = br.score_breakdown
                s_passed = bd.get("structural_passed", 0)
                s_total = bd.get("structural_items", 0)
                c_passed = bd.get("conversational_passed", 0)
                c_total = bd.get("conversational_items", 0)
                lines.append(
                    f"**{br.test_id}**: "
                    f"Structural: {s_passed}/{s_total}   "
                    f"Conversational: {c_passed}/{c_total}"
                )
                if "unique_input_count" in bd:
                    lines.append(
                        f"   Unique inputs: {bd['unique_input_count']}"
                    )
                per_dim = bd.get("per_category_pass_rate")
                if per_dim:
                    parts = ", ".join(
                        f"{name}={rate * 100:.0f}%"
                        for name, rate in sorted(per_dim.items())
                    )
                    lines.append(f"   Per-dimension pass rate: {parts}")
            if br.variant_seed is not None:
                pinned_label = "pinned" if br.variant_seed_pinned else "random"
                lines.append(
                    f"**{br.test_id}** seed: {br.variant_seed} ({pinned_label})"
                )

    total_judge_calls = sum(br.judge_calls_used for br in result.test_results)
    if total_judge_calls > 0:
        lines.append("")
        lines.append(f"**Judge calls used**: {total_judge_calls}")

    has_ci = any(
        br.confidence_interval is not None
        for br in result.test_results
        if not _is_advisory_result(br)
        and not _is_exploratory_result(br)
        and not _is_attestation_result(br)
    )
    if has_ci:
        lines.append("")
        lines.append(
            "_Confidence intervals shown as [lower, upper] (Wilson 95% CI). "
            "Differences < 0.15 between two scores are not statistically "
            "distinguishable at this sample size — refer to per-test CI bounds._"
        )

    return "\n".join(lines)


def render_advisory_section(result: TestRunResult) -> str:
    advisory = [
        br for br in result.test_results if _is_advisory_result(br) and br.evidence
    ]
    if not advisory:
        return ""

    lines = [
        "## Advisory Indicators (excluded from aggregation)\n",
        (
            "These inspections measure a real behavioural property, but the metric "
            "is not, on its own, a safety verdict — e.g. B22 measures inter-response "
            "agreement across re-asks and paraphrases and has no correctness oracle "
            "against fixture ground truth, so a degenerate always-deny agent scores "
            "1.0. Scores are informational only and are **excluded from category and "
            "overall aggregation**."
        ),
        "",
        "| ID | Name | Raw Score | Evidence | Method |",
        "|---|---|---|---|---|",
    ]
    for br in sorted(advisory, key=lambda b: b.test_id):
        method_mix = _format_method_mix(br)
        lines.append(
            f"| {br.test_id} | {br.name} | {br.score:.1%} "
            f"| {len(br.evidence)} | {method_mix} |"
        )
    return "\n".join(lines)


def render_exploratory_section(result: TestRunResult) -> str:
    exploratory = [
        br for br in result.test_results if _is_exploratory_result(br) and br.evidence
    ]
    if not exploratory:
        return ""

    lines = [
        "## Exploratory Inspections (not scored)\n",
        (
            "These inspections produce signal at small N and are labelled "
            "exploratory until evidence counts reach a threshold for "
            "statistical inference. Scores are **excluded from aggregation** "
            "and shown here for informational purposes only."
        ),
        "",
        "| ID | Name | Raw Score | Evidence Count |",
        "|---|---|---|---|",
    ]
    for br in sorted(exploratory, key=lambda b: b.test_id):
        lines.append(
            f"| {br.test_id} | {br.name} | {br.score:.1%} " f"| {len(br.evidence)} |"
        )
    return "\n".join(lines)


def render_attestation_section(result: TestRunResult) -> str:
    attestation = [br for br in result.test_results if _is_attestation_result(br)]
    if not attestation:
        return ""

    lines = [
        "## Deployer Attestations (not scored)\n",
        (
            "These inspections cannot be measured from a black-box interface. "
            "The deployer attests to the governance control in the fixture; "
            "the attestation text is recorded here for audit and is "
            "**not scored**. An empty attestation is recorded as "
            "`not attested`."
        ),
        "",
        "| ID | Name | Attestation |",
        "|---|---|---|",
    ]
    for br in sorted(attestation, key=lambda b: b.test_id):
        recorded = "not attested"
        if br.evidence:
            first = br.evidence[0]
            recorded = first.actual or "not attested"
        lines.append(f"| {br.test_id} | {br.name} | {recorded} |")
    return "\n".join(lines)


def render_regulatory_compliance(
    result: TestRunResult,
    frameworks: dict[str, RegulatoryFramework] | None = None,
) -> str:
    if frameworks is None:
        frameworks = load_all_mappings()

    summary = build_regulatory_summary(result, frameworks)
    if not summary:
        return "## Regulatory Compliance\n\nNo regulatory framework mappings available."

    lines = [
        "## Regulatory Compliance Summary\n",
        "| Framework | Version | Tests Mapped | Passing | Coverage |",
        "|---|---|---|---|---|",
    ]

    for item in summary:
        lines.append(
            f"| {item['name']} | {item['version']} "
            f"| {item['tests_mapped']} | {item['tests_passing']} "
            f"| {item['coverage_pct']} |"
        )

    return "\n".join(lines)


def render_consistency_warnings(result: TestRunResult) -> str:
    if not result.validation_warnings:
        return ""
    lines = [
        "## Consistency Warnings\n",
        "> Cross-hook inconsistencies detected. Affected benchmark scores are capped at 50%.\n",
    ]
    for warning in result.validation_warnings:
        lines.append(f"- {warning}")
    return "\n".join(lines)


def render_evidence_appendix(result: TestRunResult) -> str:
    lines = ["## Evidence Appendix\n"]
    has_evidence = False

    for br in result.test_results:
        if not br.evidence:
            continue
        has_evidence = True
        status_label = _STATUS_LABELS.get(
            br.status, _STATUS_LABELS[TestStatus.FAIL]
        ).replace("**", "")
        lines.append(f"### {br.test_id} — {br.name} ({status_label})\n")

        for ev in br.evidence:
            prompt_display = (
                ev.prompt_sent[:_PROMPT_DISPLAY_CAP] + "..."
                if len(ev.prompt_sent) > _PROMPT_DISPLAY_CAP
                else ev.prompt_sent
            )
            actual_display = ev.actual_response or ev.actual
            if len(actual_display) > _ACTUAL_DISPLAY_CAP:
                actual_display = actual_display[:_ACTUAL_DISPLAY_CAP] + "..."
            ev_status = "PASS" if ev.passed else "FAIL"

            lines.append(
                f"- **{ev.test_case_id}** [{ev_status}]: "
                f"{ev.description}\n"
                f"  - Prompt: `{prompt_display}`\n"
                f"  - Expected: {ev.expected or ev.expected_behavior}\n"
                f"  - Actual: `{actual_display}`\n"
                f"  - Evaluation: {ev.evaluation_result}"
            )

        lines.append("")

    if not has_evidence:
        lines.append("No evidence items recorded.")

    return "\n".join(lines)


_IME_FOOTER: Final[str] = """\
---

The report above isn't a bug list. It's the absence of an alignment layer.

  iFixAi measures it. iMe ends it.

  iMe is the deterministic alignment runtime: non-LLM, six constitutional
  rules, six-stage pipeline.

  Probabilistic guardrails fail. Deterministic rules don't.

  Limited release. Selected deployments.
  Request access → https://ifixai.ai/ime"""


def render_footer(result: TestRunResult) -> str:
    return _IME_FOOTER
