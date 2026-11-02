import json
import re
from datetime import datetime, timezone
from typing import Final

from ifixai.core.types import (
    JudgeErrorKind,
    NotApplicable,
    RegulatoryFramework,
    TestResult,
    TestRunResult,
    TestStatus,
    UngradedReason,
)
from ifixai.judge.config import JudgeConfig
from ifixai.mappings.loader import load_all_mappings
from ifixai.providers.secrets import scrub_secrets
from ifixai.reporting.health import (
    UNGRADED,
    counts_toward_score,
    gating_results,
    measured_count,
    run_health,
)
from ifixai.reporting.judge_votes import describe_votes
from ifixai.reporting.rates import per_class_line
from ifixai.reporting.regulatory import (
    build_regulatory_json_section,
    build_regulatory_summary,
    get_test_regulatory_mappings,
)
from ifixai.scoring.mandatory_minimums import MANDATORY_MINIMUMS, PASS_THRESHOLD
from ifixai.shared.evidence import DECLARED_KEY, HOOK_NOTE_KEY

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
SUBSTITUTE_JUDGE_PREFIX: Final[str] = "substitute judge graded this run: "
JUDGE_TRANSPORT_FAILURE_PREFIX: Final[str] = "judge calls that failed and were retried: "
JUDGE_DEFAULT_TEMPERATURE_MESSAGE: Final[str] = (
    "judge ran at its default temperature, not 0: its model takes no temperature "
    "setting, so verdicts on temperature-0 inspections may vary between runs"
)
SUT_DEFAULT_TEMPERATURE_MESSAGE: Final[str] = (
    "agent ran at its default temperature: its model takes no temperature setting, "
    "so B22 treats its sampling as nondeterministic"
)
# Each default-temperature warning and its label in the summary report.
DEFAULT_TEMPERATURE_WARNINGS: Final[dict[str, str]] = {
    JUDGE_DEFAULT_TEMPERATURE_MESSAGE: "Judge temperature",
    SUT_DEFAULT_TEMPERATURE_MESSAGE: "Agent temperature",
}


def scoped_gate_decides(result: TestRunResult) -> bool:
    """A run that left out a mandatory inspection is decided by the ones it selected,
    unless it stopped early or measured almost nothing (see `run_verdict`)."""
    return (
        bool(result.mandatory_minimums_not_run)
        and bool(gating_results(result))
        and not result.partial
        and not run_health(result).invalid
        and not unchecked_minimums(result)
    )


def absent_score_reason(result: TestRunResult) -> str:
    """Why a run has no headline score; every terminal and report surface shares it."""
    if result.partial:
        return "the run stopped early, so there is no headline score."
    ran = {r.test_id for r in result.test_results}
    not_run = sorted(result.mandatory_minimums_not_run)
    unchecked = unchecked_minimums(result)
    if unchecked:
        by_reason: dict[str, list[str]] = {}
        for item in unchecked:
            by_reason.setdefault(item.reason, []).append(item.test_id)
        why = "; ".join(f"{_listed(sorted(ids))} ({reason})" for reason, ids in by_reason.items())
        return f"this run couldn't check safety gate {why}, so there is no headline score."
    unselected = ", ".join(t for t in not_run if t not in ran)
    # A minimum that ran but an outage left ungraded withholds the score too, never as a fail.
    ungraded = ", ".join(t for t in not_run if t in ran)
    if ungraded:
        also = f" and {unselected} not selected" if unselected else ""
        return f"safety gate {ungraded} not evaluated{also}, so there is no headline score."
    gating = gating_results(result)
    if unselected and gating:
        kind = "strategic" if result.run_mode == "strategic" else "scoped"
        reason = (
            f"{unselected} were not selected in this {kind} run, so there is no "
            "headline score."
        )
        if scoped_gate_decides(result):
            reason += " The exit code reflects only the selected inspections."
        return reason
    if result.test_results and not gating:
        return (
            "only report-only inspections (exploratory, advisory or attestation) "
            "were selected, so there is nothing to grade."
            + (f" Not selected: {unselected}." if unselected else "")
        )
    return "insufficient evidence across all inspections."


def compute_insights(result: TestRunResult) -> dict[str, object]:
    """Derive presentation-ready insights shared by console and file reports."""
    results = result.test_results

    status_counts = {"pass": 0, "fail": 0, "inconclusive": 0, "error": 0}
    for br in results:
        status_counts[br.status.value] = status_counts.get(br.status.value, 0) + 1

    scored = [
        (cs.category.value, cs.score)
        for cs in result.category_scores
        if cs.score is not None
    ]
    weakest = sorted(scored, key=lambda pair: pair[1])[:3]

    total_categories = len(result.category_scores)
    scored_categories = len(scored)

    # A findings-only result has no score, so it carries neither a score nor a pass mark.
    exploratory = [
        {"test_id": br.test_id, "name": br.name, "findings_only": True}
        if br.findings_only
        else {
            "test_id": br.test_id,
            "name": br.name,
            "score": br.score,
            "passing": br.passing,
        }
        for br in results
        if br.spec is not None and br.spec.is_exploratory
    ]

    delta = None
    if (
        not result.self_judged
        and result.overall_score is not None
        and result.strategic_score is not None
    ):
        delta = result.overall_score - result.strategic_score

    return {
        "self_judged": result.self_judged,
        "status_counts": status_counts,
        "total_tests": len(results),
        "weakest_pillars": weakest,
        "scored_categories": scored_categories,
        "total_categories": total_categories,
        "exploratory": exploratory,
        "overall_score": result.overall_score,
        "strategic_score": result.strategic_score,
        "grade": "n/a" if result.overall_score is None else result.grade.value,
        "strategic_overall_delta": delta,
        "passed": run_verdict(result) == "PASS",
    }


def render_insights(result: TestRunResult) -> str:
    """Markdown "Insights" section derived from :func:`compute_insights`."""
    ins = compute_insights(result)
    sc = ins["status_counts"]
    lines = ["## Insights", ""]
    lines.append(
        f"- **Tests:** {sc['pass']} passed · {sc['fail']} failed · "
        f"{sc['inconclusive']} inconclusive"
        + (f" · {sc['error']} error" if sc["error"] else "")
    )
    lines.append(
        f"- **Category coverage:** {ins['scored_categories']}/"
        f"{ins['total_categories']} categories scored"
    )
    if not ins["self_judged"] and ins["weakest_pillars"]:
        weakest = ", ".join(
            f"{name} ({score:.0%})" for name, score in ins["weakest_pillars"]
        )
        lines.append(f"- **Weakest pillars:** {weakest}")
    if not ins["self_judged"] and ins["strategic_overall_delta"] is not None:
        delta = ins["strategic_overall_delta"]
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"- **Overall vs strategic:** {ins['overall_score']:.1%} overall "
            f"vs {ins['strategic_score']:.1%} strategic ({sign}{delta:.1%})"
        )
    if ins["exploratory"]:
        names = ", ".join(item["test_id"] for item in ins["exploratory"])
        lines.append(
            f"- **Exploratory (not scored):** {len(ins['exploratory'])} "
            f"inspection(s) — {names}"
        )
    return "\n".join(lines)


def insufficient_evidence_warnings(
    test_results: list[TestResult],
) -> list[str]:
    messages: list[str] = []
    for br in test_results:
        if not br.insufficient_evidence:
            continue
        floor = br.spec.min_evidence_items if br.spec else 10
        # The floor's own count: a budget-capped run graded only some of its items.
        got = measured_count(
            br.evidence, bool(br.spec and br.spec.count_extraction_errors_as_fail)
        )
        messages.append(
            INSUFFICIENT_EVIDENCE_PREFIX + f"{br.test_id} (got {got}, min {floor})"
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
        affected = sum(
            1 for ev in br.evidence
            if ev.extraction_error is not None and ev.extraction_error != JudgeErrorKind.BUDGET
        )
        if affected:
            messages.append(
                EXTRACTION_ERROR_PREFIX
                + f"{br.test_id} ({affected} evidence items affected)"
            )
        skipped = sum(ev.extraction_error == JudgeErrorKind.BUDGET for ev in br.evidence)
        if skipped:
            messages.append(
                f"judge budget exhausted: {br.test_id} "
                f"({skipped} evidence items skipped without a judge call)"
            )
    return messages


def judge_substitution_warnings(judge_stats: dict | None) -> list[str]:
    """Surface that a fallback model, not the configured judge, produced grades.

    A grade is only as citable as its origin. When the configured judge dies and
    the fallback chain silently takes over, the scorecard would otherwise print a
    clean A with no hint that a different — usually cheaper — model did the
    grading, and no hint that N calls failed on the way there.
    """
    if not judge_stats:
        return []
    messages: list[str] = []
    fallbacks = judge_stats.get("fallback_grades") or {}
    if isinstance(fallbacks, dict) and fallbacks:
        configured = judge_stats.get("judge_model") or "the configured judge"
        breakdown = ", ".join(
            f"{model} ({count} verdict{'s' if count != 1 else ''})"
            for model, count in sorted(fallbacks.items())
        )
        messages.append(
            SUBSTITUTE_JUDGE_PREFIX
            + f"{breakdown}. {configured} gave no verdict on those probes "
            "(a failed call or a cut-off reply). "
            "Scores are not comparable to a run graded by the configured judge."
        )
    failures = judge_stats.get("judge_transport_failures") or {}
    if isinstance(failures, dict) and failures:
        total = sum(int(c) for c in failures.values())
        breakdown = ", ".join(
            f"{model} ({count})" for model, count in sorted(failures.items())
        )
        messages.append(
            JUDGE_TRANSPORT_FAILURE_PREFIX + f"{total} total — {breakdown}"
        )
    if judge_stats.get("default_temperature"):
        messages.append(JUDGE_DEFAULT_TEMPERATURE_MESSAGE)
    return messages


def b22_determinism_warning(
    test_results: list[TestResult],
    sut_temperature: float | None,
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


# Providers that front many vendors behind one name. For these the real grading
# vendor is the model slug's prefix, so a cross-vendor judge routed through one
# aggregator (e.g. a Gemini SUT graded by a Claude judge, both via OpenRouter,
# Requesty, Cloudflare AI Gateway or Vercel AI Gateway) is recognized as
# independent instead of mislabeled "self-judge".
_AGGREGATOR_PROVIDERS: Final[frozenset[str]] = frozenset(
    {"openrouter", "orcarouter", "requesty", "cloudflare", "vercel", "atlascloud", "litellm", "http", "langchain", "huggingface"}
)

# Namespaces that sit in front of the author, as in the Workers AI ids
# "@cf/meta/llama-3.3-70b-instruct-fp8-fast" and "@hf/google/gemma-7b-it", which
# Cloudflare AI Gateway also accepts behind its own "workers-ai/" prefix: the
# vendor is the first segment that is not a namespace.
CATALOG_NAMESPACE_PREFIXES: Final[frozenset[str]] = frozenset(
    {"workers-ai", "@cf", "@hf"}
)

# Distinct provider slugs that front the SAME underlying model vendor, so a
# self-judge across them is still same-vendor (biased): Azure OpenAI serves
# OpenAI models; the direct Gemini API and a "google/..." slug are both Google.
# "google-ai-studio" and "grok" are the names Cloudflare AI Gateway routes Google
# and xAI models under. The rest are one vendor spelled two ways: gateways
# disagree on xAI and Mistral (Workers AI alone uses both Mistral spellings), and
# Workers AI names Meta, DeepSeek and Z.ai by their Hugging Face organisation.
# Without the alias a Llama judge on one gateway reads as independent of a Llama
# system under test on another.
_VENDOR_ALIASES: Final[dict[str, str]] = {
    "azure": "openai",
    "gemini": "google",
    "google-ai-studio": "google",
    "grok": "xai",
    "x-ai": "xai",
    "mistralai": "mistral",
    "meta-llama": "meta",
    "deepseek-ai": "deepseek",
    "zai-org": "z-ai",
}

# Aggregator prefixes that name the cloud hosting the model, not its developer
# ("bedrock/claude-sonnet-4-6@eu-central-1"), so the vendor comes from the family.
_CLOUD_HOST_PREFIXES: Final[frozenset[str]] = frozenset(
    {"bedrock", "vertex", "vertex_ai", "azure", "deepinfra", "coding"}
)
_MODEL_FAMILY_VENDORS: Final[dict[str, str]] = {
    "claude": "anthropic",
    "gemini": "google",
    "gpt": "openai",
}


def grading_vendor(provider: str, model: str | None) -> str:
    p = (provider or "").lower()
    if p == "bedrock" and model:
        # Native foundation-model IDs declare the creator before a dot, with
        # an optional cross-region prefix. Only named foundation models and
        # system inference profiles in ARNs carry that identity; application
        # profiles and custom model ARNs are opaque and must remain unresolved.
        identifier = model.lower()
        if identifier.startswith("arn:"):
            resource = identifier.split(":", 5)[-1]
            if resource.startswith(("foundation-model/", "inference-profile/")):
                identifier = resource.partition("/")[2]
            else:
                identifier = ""
        parts = identifier.split(".")
        if parts[0] in {"us", "eu", "apac", "global"}:
            parts = parts[1:]
        raw = parts[0] if len(parts) > 1 else p
    elif p in _AGGREGATOR_PROVIDERS and model and "/" in model:
        raw, _, rest = model.lower().partition("/")
        while raw in CATALOG_NAMESPACE_PREFIXES and "/" in rest:
            raw, _, rest = rest.partition("/")
        if raw in _CLOUD_HOST_PREFIXES:
            raw = next((v for f, v in _MODEL_FAMILY_VENDORS.items() if f in rest), raw)
    else:
        raw = p
    return _VENDOR_ALIASES.get(raw, raw)


def self_judge_bias_applies(
    judge_config: JudgeConfig | None,
    model_provider: str,
    model_model: str | None = None,
) -> bool:
    """True when no judge is from a vendor distinct from the system under test, so
    the grade is self/same-vendor (biased). A single independent cross-vendor judge
    is NOT biased. Vendor is resolved with `grading_vendor`, so aggregator providers
    (OpenRouter, etc.) do not collapse different underlying vendors into one."""
    if judge_config is None:
        return True
    if judge_config.providers:
        judges = [(p.provider, p.model) for p in judge_config.providers]
    elif judge_config.provider:
        judges = [(judge_config.provider, judge_config.model)]
    else:
        return True
    sut_vendor = grading_vendor(model_provider, model_model)
    judge_vendors = {grading_vendor(p, m) for p, m in judges}
    return not any(v != sut_vendor for v in judge_vendors)


def scorecard_warnings(
    judge_config: JudgeConfig | None,
    model_provider: str,
    model_model: str | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    warnings: list[str] = list(extra) if extra else []
    if self_judge_bias_applies(judge_config, model_provider, model_model):
        if SELF_JUDGE_BIAS_ADVISORY not in warnings:
            warnings.append(SELF_JUDGE_BIAS_ADVISORY)
    return warnings


def generate_json_report(result: TestRunResult) -> str:
    frameworks = load_all_mappings()

    report = {
        "metadata": build_metadata_section(result, frameworks),
        "partial": result.partial,
        "abort_reason": result.abort_reason,
        "not_run_test_ids": list(result.not_run_test_ids),
        "not_run_reason": result.not_run_reason if result.not_run_test_ids else None,
        "not_applicable": [item.model_dump(mode="json") for item in result.not_applicable],
        "resumed_run_id": result.resumed_run_id,
        "reused_result_count": result.reused_result_count,
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
        "insights": compute_insights(result),
    }

    return json.dumps(report, indent=2, ensure_ascii=False)


def render_partial_banner(result: TestRunResult) -> str:
    if not result.partial:
        return ""
    return (
        "> ⚠️ **PARTIAL RUN** — this run stopped early "
        f"({result.abort_reason or 'unknown reason'}). Scores cover only the "
        f"{len(result.test_results)} inspection(s) that finished and are not "
        "comparable to a full run. Resume the rest with `--resume <run id>`."
    )


def render_resumed_banner(result: TestRunResult) -> str:
    if not result.resumed_run_id:
        return ""
    return (
        f"> ℹ️ **Resumed run** — {result.reused_result_count} of "
        f"{len(result.test_results)} inspection results were reused from an "
        f"earlier session of run `{result.resumed_run_id}`. Judge-call stats "
        "cover only the final session; reused results keep the grading of "
        "the judge that originally ran them."
    )


def render_default_temperature_banner(result: TestRunResult) -> str:
    return "\n\n".join(
        f"> ⚠️ **{label}**: {message}."
        for message, label in DEFAULT_TEMPERATURE_WARNINGS.items()
        if message in result.warnings
    )


def render_not_run_section(result: TestRunResult) -> str:
    """The planned inspections an aborted run never reached, so the report
    documents the whole plan instead of reading like half a scorecard."""
    if not result.not_run_test_ids:
        return ""
    from ifixai.harness.registry import SPEC_BY_ID

    n = len(result.not_run_test_ids)
    lines = [
        f"## Not run ({n} inspection{'s' if n != 1 else ''})\n",
        "The run stopped before these finished; they carry no score. "
        "Resume with `--resume <run id>` to complete them.\n",
        "| ID | Inspection | Status |",
        "|---|---|---|",
    ]
    for tid in result.not_run_test_ids:
        spec = SPEC_BY_ID.get(tid)
        name = getattr(spec, "name", tid) if spec is not None else tid
        lines.append(f"| {tid} | {name} | not run ({result.not_run_reason}) |")
    return "\n".join(lines)


def render_not_applicable_section(result: TestRunResult) -> str:
    """The inspections set aside before the run, each with why. Never scored."""
    if not result.not_applicable:
        return ""
    lines = [
        f"## Not applicable ({len(result.not_applicable)})\n",
        "Set aside before the run; they count toward no score.\n",
        "| ID | Inspection | Why |",
        "|---|---|---|",
    ]
    lines.extend(f"| {item.test_id} | {item.name} | {item.reason} |" for item in result.not_applicable)
    return "\n".join(lines)


def render_run_warnings(result: TestRunResult) -> str:
    """Keep operator and grading caveats with exported results."""
    if not result.warnings:
        return ""
    return "## Run Warnings\n\n" + "\n".join(f"- {warning}" for warning in result.warnings)


def generate_markdown_report(result: TestRunResult) -> str:
    frameworks = load_all_mappings()

    sections = [
        render_header(result),
        render_findings_section(result),
        render_partial_banner(result),
        render_resumed_banner(result),
        render_summary(result),
        render_insights(result),
        render_category_table(result),
        render_mandatory_minimums(result),
        render_run_warnings(result),
        render_consistency_warnings(result),
        render_test_table(result),
        render_not_run_section(result),
        render_not_applicable_section(result),
        render_advisory_section(result),
        render_exploratory_section(result),
        render_attestation_section(result),
        render_regulatory_compliance(result, frameworks),
        render_evidence_appendix(result),
    ]

    return "\n\n".join(s for s in sections if s) + "\n"


def _render_top_failures(result: TestRunResult, limit: int = 8) -> str:
    """A compact 'where it hurts most' list — lowest-scoring failed inspections."""
    failed = [
        br
        for br in result.test_results
        if br.status == TestStatus.FAIL and not br.insufficient_evidence
    ]
    if not failed:
        return ""
    failed.sort(key=lambda br: br.score)
    lines = ["## Top failures", ""]
    for br in failed[:limit]:
        name = br.name or br.test_id
        if br.findings_only:
            lines.append(
                f"- **{br.test_id}** {name} — findings, report only ({br.category.value})"
            )
            continue
        lines.append(
            f"- **{br.test_id}** {name} — {br.score:.0%} "
            f"(threshold {br.threshold:.0%}, {br.category.value})"
        )
    remaining = len(failed) - limit
    if remaining > 0:
        lines.append(f"- …and {remaining} more (see the full report).")
    return "\n".join(lines)


def generate_summary_report(result: TestRunResult) -> str:
    """Short scannable report: headline, insights, categories, top failures."""
    sections = [
        render_header(result),
        render_findings_section(result),
        render_partial_banner(result),
        render_resumed_banner(result),
        render_default_temperature_banner(result),
        render_summary(result),
        render_insights(result),
        render_category_table(result),
        render_mandatory_minimums(result),
        _render_top_failures(result),
        render_not_run_section(result),
        render_not_applicable_section(result),
        "_This is the summary. The full report (with per-inspection evidence) "
        "is the companion `.md` without the `-summary` suffix._",
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
        # No overall score means no grade (as in the Markdown and console views).
        "grade": "n/a" if overall is None else result.grade.value,
        "strategic_score": round(result.strategic_score, 4),
        "strategic_score_pct": f"{result.strategic_score:.1%}",
        # The verdict the exit code reads (`run_exit_code`), at the run's --min-score; the
        # runner's own `passed` is fixed at PASS_THRESHOLD and never passes a scoped run.
        "passed": run_verdict(result) == "PASS",
        "verdict": run_verdict(result).lower(),
        # The pass mark the overall score is read against (--min-score, else PASS_THRESHOLD).
        "min_score": pass_mark(result),
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
            "test_count": cs.test_count,
            "test_ids": cs.test_ids,
        }
        for cs in result.category_scores
    ]


def build_mandatory_minimums_section(
    result: TestRunResult,
) -> dict[str, object]:
    return {
        # all_passed means "nothing that ran failed the gate", which is vacuously
        # true when nothing ran. Read it with `evaluated`.
        "all_passed": result.mandatory_minimums_passed,
        "evaluated": not result.mandatory_minimums_not_run,
        "any_inconclusive": bool(result.mandatory_minimums_inconclusive),
        "per_test": {
            test_id: status.value
            for test_id, status in result.mandatory_minimum_status.items()
        },
        "violations": list(result.mandatory_minimum_violations),
        "inconclusive": list(result.mandatory_minimums_inconclusive),
        "not_run": list(result.mandatory_minimums_not_run),
        # Set aside before the run: never gated, so `all_passed` and `per_test` say nothing of them.
        "set_aside": {item.test_id: item.reason for item in set_aside_minimums(result)},
    }


def scrubbed(value: object) -> object:
    """Every string inside a JSON-shaped value with secrets scrubbed, its shape unchanged."""
    if isinstance(value, str):
        return scrub_secrets(value)
    if isinstance(value, dict):
        return {key: scrubbed(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrubbed(item) for item in value]
    return value


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
            # Keep the provenance used by runners to distinguish diagnostics
            # and unscorable probes from behavioral failures. Pydantic's JSON
            # mode also converts any enums nested in the structured details.
            ev_dict.update(ev.model_dump(
                mode="json", include={"details", "extraction_error", "is_diagnostic"}
            ))
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
                rubric_dict: dict[str, object] = {
                    "weighted_score": ev.rubric_verdict.weighted_score,
                    "weighted_score_pre_veto": ev.rubric_verdict.weighted_score_pre_veto,
                    "mandatory_veto": ev.rubric_verdict.mandatory_veto,
                    "passed": ev.rubric_verdict.passed,
                    "verdict": ev.rubric_verdict.verdict,
                }
                # A findings-only inspection publishes its verdicts, never a score, per chat or overall.
                if br.findings_only:
                    del rubric_dict["weighted_score"], rubric_dict["weighted_score_pre_veto"]
                ev_dict["rubric_verdict"] = rubric_dict
            # A findings-only inspection's items carry whole transcripts, so its agent replies are
            # scrubbed as they are on the HTML card.
            evidence_list.append(scrubbed(ev_dict) if br.findings_only else ev_dict)

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
        if br.findings_only:
            for key in ("score", "score_pct", "threshold", "passing"):
                del br_dict[key]
            br_dict["findings_only"] = True
        if br.findings:
            br_dict["findings"] = [scrub_secrets(line) for line in br.findings]
        if br.error_message:
            br_dict["error_message"] = br.error_message
        br_dict["evaluation_path"] = _dominant_evaluation_path(br)
        # An interval around a score is a score; a findings-only result withholds it, as the HTML does.
        if br.confidence_interval and not br.findings_only and not unscored:
            br_dict["ci_lower"] = round(br.confidence_interval.lower, 4)
            br_dict["ci_upper"] = round(br.confidence_interval.upper, 4)
            br_dict["confidence_interval"] = {
                "lower": br.confidence_interval.lower,
                "upper": br.confidence_interval.upper,
                "method": br.confidence_interval.method,
                "sample_size": br.confidence_interval.sample_size,
                "warning": br.confidence_interval.warning,
            }
        if br.score_breakdown is not None:
            br_dict["score_breakdown"] = br.score_breakdown
        if br.variant_seed is not None:
            br_dict["variant_seed"] = br.variant_seed
            br_dict["variant_seed_pinned"] = br.variant_seed_pinned
        if br.evaluation_mode:
            br_dict["evaluation_mode"] = br.evaluation_mode.value
        items.append(br_dict)
    return items


def format_evaluation_date(value: datetime) -> str:
    """Display aware timestamps in UTC; legacy naive run dates already use UTC."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M UTC")


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
        f"**Evaluation Date:** {format_evaluation_date(result.evaluation_date)}  \n"
        f"**Run Mode:** {result.run_mode}  \n"
        f"**Evaluation Mode:** {eval_mode}"
    )
    return header


def set_aside_minimums(result: TestRunResult) -> list[NotApplicable]:
    """The mandatory minimums set aside before the run: never gated, but always shown. One the
    gate holds a status for is left out, so a FAIL or INCONCLUSIVE never shows as not applicable."""
    return [
        item
        for item in result.not_applicable
        if item.test_id in MANDATORY_MINIMUMS and item.test_id not in result.mandatory_minimum_status
    ]


def unchecked_minimums(result: TestRunResult) -> list[NotApplicable]:
    """The mandatory minimums this run set aside because it couldn't check them (no judge, the
    purpose check): not run, so the grade is withheld and the scoped gate never decides."""
    not_run = set(result.mandatory_minimums_not_run)
    return [item for item in result.not_applicable if item.test_id in not_run]


def _listed(test_ids: list[str]) -> str:
    """`B01`, `B01 and P01`, `B01, B08 and P01`."""
    return test_ids[0] if len(test_ids) == 1 else f"{', '.join(test_ids[:-1])} and {test_ids[-1]}"


def gate_label(label: str, result: TestRunResult) -> str:
    """`label` when every mandatory minimum was gated; else what it covers and what was set aside,
    `PASS on B08; B01 and P01 not applicable`, so a PASS never stands for checks that never ran."""
    set_aside = [item.test_id for item in set_aside_minimums(result)]
    if not set_aside:
        return label
    gated = sorted(result.mandatory_minimum_status)
    if not gated:
        return f"{_listed(set_aside)} not applicable"
    return f"{label} on {_listed(gated)}; {_listed(set_aside)} not applicable"


def minimums_label(result: TestRunResult) -> str:
    statuses = result.mandatory_minimum_status.values()
    if any(s == TestStatus.FAIL for s in statuses):
        label = "FAIL"
    elif result.partial and result.mandatory_minimums_not_run:
        label = "NOT CHECKED (the run stopped early)"
    elif any(s == TestStatus.INCONCLUSIVE for s in statuses):
        label = "INCONCLUSIVE"
    else:
        label = "PASS"
    return gate_label(label, result)


def run_verdict(result: TestRunResult) -> str:
    """The run's verdict. The terminal, the styled card, every report and the CLI's exit code
    (`run_exit_code`) all read this one, so they never disagree.

    PASS or FAIL is the overall score against the run's `min_score`, or for a scoped run that left
    out a mandatory minimum, its selected inspections. A mandatory minimum that ran and failed
    fails the run, stopped or not: no rerun undoes that finding. A run that stopped early or
    measured almost nothing (RUN INVALID), or that has no score to read, neither passes nor fails:
    ERROR when an inspection errored, else INCONCLUSIVE. An errored inspection never turns a PASS
    or a FAIL into ERROR; it is listed with the errors.
    """
    if result.mandatory_minimum_violations:
        return "FAIL"
    mark = verdict_pass_mark(result)
    if mark is not None and result.overall_score is not None:
        if result.overall_score >= mark:
            return "PASS"
        return "INCONCLUSIVE" if result.mandatory_minimums_inconclusive else "FAIL"
    if result.overall_score is None and scoped_gate_decides(result):
        return _selected_verdict(result)
    errored = any(br.status == TestStatus.ERROR for br in result.test_results)
    return "ERROR" if errored else "INCONCLUSIVE"


def pass_mark(result: TestRunResult) -> float:
    """The overall score a run needs to pass: its --min-score, else `PASS_THRESHOLD`."""
    return PASS_THRESHOLD if result.min_score is None else result.min_score


def verdict_pass_mark(result: TestRunResult) -> float | None:
    """The pass mark `run_verdict` read the overall score against, or None when the score didn't
    decide: a failed mandatory minimum, a stopped or RUN INVALID run, or no overall score."""
    if (
        result.mandatory_minimum_violations
        or result.partial
        or result.overall_score is None
        or run_health(result).invalid
    ):
        return None
    return pass_mark(result)


def pass_mark_note(result: TestRunResult) -> str:
    """` (pass mark 10%)` after the verdict wherever it is shown, when the score decided it
    (`verdict_pass_mark`); empty otherwise. The verdict follows --min-score, so it names the mark."""
    mark = verdict_pass_mark(result)
    return "" if mark is None else f" (pass mark {mark:.1%})".replace(".0%", "%")


def _selected_verdict(result: TestRunResult) -> str:
    """A scoped run's verdict: PASS only when every selected inspection that can gate passed."""
    statuses = {r.status for r in gating_results(result)}
    if statuses == {TestStatus.PASS}:
        return "PASS"
    if TestStatus.FAIL in statuses:
        return "FAIL"
    return "ERROR" if TestStatus.ERROR in statuses else "INCONCLUSIVE"


def run_exit_code(result: TestRunResult) -> int:
    """The CLI's exit code, read off `run_verdict`: 0 only for PASS, 2 for FAIL. A run that neither
    passed nor failed exits 1 when it stopped early (resume it), else 2."""
    verdict = run_verdict(result)
    if verdict == "PASS":
        return 0
    return 1 if result.partial and verdict != "FAIL" else 2


def run_verdict_label(result: TestRunResult) -> str:
    """The verdict as every surface words it. It says when the run stopped early and names any
    mandatory minimum left out or left ungraded, so a PASS never stands for checks that never ran;
    "on the selected inspections" only when the run left a minimum out of its selection."""
    verdict = run_verdict(result)
    if result.partial:
        return f"{verdict} (the run stopped early)"
    if not scoped_gate_decides(result):
        return verdict
    ran = {r.test_id for r in result.test_results}
    not_run = sorted(result.mandatory_minimums_not_run)
    unselected = [t for t in not_run if t not in ran]
    ungraded = [t for t in not_run if t in ran]
    notes = [f"{_listed(unselected)} not in this run"] if unselected else []
    if ungraded:
        notes.append(f"{_listed(ungraded)} not graded")
    head = f"{verdict} on the selected inspections" if unselected else verdict
    return "; ".join([head, *notes])


def render_summary(result: TestRunResult) -> str:
    minimums_status = minimums_label(result)
    verdict = run_verdict_label(result) + pass_mark_note(result)
    overall_display = (
        f"n/a: {absent_score_reason(result)}"
        if result.overall_score is None
        else f"{result.overall_score:.1%}"
    )

    return (
        f"## Overall Score\n\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| **Overall Score** | {overall_display} |\n"
        f"| **Grade** | {'n/a' if result.overall_score is None else result.grade.value} |\n"
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
    set_aside = set_aside_minimums(result)
    if not result.mandatory_minimum_status and not set_aside:
        return "## Mandatory Minimums\n\nNo mandatory minimums defined."

    lines = [
        "## Mandatory Minimums\n",
        "| Test | Status |",
        "|---|---|",
    ]

    not_run = set(result.mandatory_minimums_not_run)
    for test_id, status_value in sorted(result.mandatory_minimum_status.items()):
        status = _STATUS_LABELS.get(status_value, _STATUS_LABELS[TestStatus.FAIL])
        # "Never selected" and "ran but could not tell" are both INCONCLUSIVE in
        # the status map. Only the second is a finding about the agent, so say
        # which one this is rather than let a reader assume the gate was tried.
        if test_id in not_run:
            ran = next((r for r in result.test_results if r.test_id == test_id), None)
            unchecked = next((item for item in unchecked_minimums(result) if item.test_id == test_id), None)
            if unchecked is not None:
                status = f"NOT RUN ({unchecked.reason})"
            elif test_id in result.not_run_test_ids:
                status = "NOT RUN (the run stopped before it)"
            elif ran is None:
                status = "NOT RUN (not selected for this run)"
            elif ran.ungraded_reason is UngradedReason.AGENT_UNAVAILABLE:
                status = "NOT RUN (the agent did not answer)"
            else:
                status = "NOT RUN (the judge could not grade it)"
        lines.append(f"| {test_id} | {status} |")
    lines.extend(f"| {item.test_id} | NOT APPLICABLE ({item.reason}) |" for item in set_aside)

    if not_run:
        lines.append(
            "\n_A mandatory gate that did not run leaves the run ungradeable, so "
            "Overall Score and Grade are withheld rather than computed without it._"
        )

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
    # An empty SUT reply is INCONCLUSIVE with ``error`` set (see BaseTest.run);
    # keep the label consistent with the JSON and HTML exports.
    if br.error and br.status != TestStatus.INCONCLUSIVE:
        return _STATUS_LABELS[TestStatus.ERROR]
    return _STATUS_LABELS.get(br.status, _STATUS_LABELS[TestStatus.FAIL])


def _format_method_mix(br: TestResult) -> str:
    """How the items a score can count were graded. Diagnostics never count; hook notes are
    named apart, so a fixture's answers never read as structural scoring."""
    if not br.evidence:
        return "—"
    counts: dict[str, int] = {}
    for ev in br.evidence:
        if ev.is_diagnostic:
            continue
        method = ev.evaluation_method.value
        counts[method] = counts.get(method, 0) + 1
    parts = [f"{n}× {m}" for m, n in sorted(counts.items())]
    notes = [ev for ev in br.evidence if ev.details.get(HOOK_NOTE_KEY)]
    if notes:
        source = "fixture-answered " if any(ev.details.get(DECLARED_KEY) for ev in notes) else ""
        parts.append(f"{len(notes)} {source}hook notes, not scored")
    return ", ".join(parts) or "—"


def _dominant_evaluation_path(br: TestResult) -> str:
    """Summarise the evaluation path(s) of the items a score can count (diagnostics never do)."""
    methods: set[str] = {ev.evaluation_method.value for ev in br.evidence if not ev.is_diagnostic}
    if not methods:
        return "NONE"
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
                    # A findings-only inspection keeps its verdicts but publishes no score.
                    weighted = "" if br.findings_only else f"Weighted score: {rv.weighted_score:.2f} | "
                    lines.append(f"- {weighted}Verdict: {rv.verdict}{veto_note}\n")

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
                    lines.append(f"   Unique inputs: {bd['unique_input_count']}")
                per_dim = bd.get("per_category_pass_rate")
                if per_dim:
                    parts = ", ".join(
                        f"{name}={rate * 100:.0f}%"
                        for name, rate in sorted(per_dim.items())
                    )
                    lines.append(f"   Per-dimension pass rate: {parts}")
                per_class = bd.get("per_class_pass_rate")
                if per_class:
                    lines.append(f"   Pass rate by class: {per_class_line(per_class)}")
            if br.variant_seed is not None:
                pinned_label = "pinned" if br.variant_seed_pinned else "random"
                lines.append(
                    f"**{br.test_id}** seed: {br.variant_seed} ({pinned_label})"
                )

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
            f"| {br.test_id} | {br.name} | {raw_score_cell(br)} "
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
            f"| {br.test_id} | {br.name} | {raw_score_cell(br)} | {len(br.evidence)} |"
        )
    return "\n".join(lines)


# What markdown reads as markup mid-line, and what starts a list or heading at a line's start.
MARKDOWN_INLINE_RE = re.compile(r"([\\`*_\[\]<>#|~])")
MARKDOWN_LEADING_RE = re.compile(r"^([-+=])|^(\d+)([.)])")


def markdown_line(text: str) -> str:
    """Plain text, which may quote an agent's reply, as one literal markdown line: whitespace runs
    collapsed so it cannot open a heading or block, and markup characters backslash-escaped."""
    escaped = MARKDOWN_INLINE_RE.sub(r"\\\1", " ".join(text.split()))
    return MARKDOWN_LEADING_RE.sub(
        lambda m: f"\\{m.group(1)}" if m.group(1) else f"{m.group(2)}\\{m.group(3)}", escaped
    )


def longest_backtick_run(text: str) -> int:
    return max((len(run) for run in re.findall(r"`+", text)), default=0)


def code_span(text: str) -> str:
    """`text` as one inline code span whose fence is longer than any backtick run inside it, padded
    when the text starts or ends with a backtick, so the text cannot close the span early."""
    fence = "`" * (longest_backtick_run(text) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def actual_markdown(text: str) -> str:
    """An appendix item's Actual. One line stays an inline code span; text with line breaks, such as a
    transcript, becomes a fenced block indented under the item, so a reply's blank lines, headings or
    list markers stay inside it instead of ending the list."""
    if "\n" not in text:
        return code_span(text)
    fence = "`" * max(3, longest_backtick_run(text) + 1)
    body = "\n".join(f"    {line}" if line.strip() else "" for line in text.split("\n"))
    return f"\n    {fence}\n{body}\n    {fence}"


def findings_block(br: TestResult) -> list[str]:
    """A report-only inspection's findings, for the top of its section: empty when it has none."""
    if not br.findings:
        return []
    return ["**Findings:**\n", *(f"- {markdown_line(scrub_secrets(line))}" for line in br.findings), ""]


def render_findings_section(result: TestRunResult) -> str:
    """What each report-only inspection found, at the head of the report and the summary: it has no
    score to stand for it."""
    with_findings = [br for br in result.test_results if br.findings]
    if not with_findings:
        return ""
    lines = ["## Findings (report only)", ""]
    for br in with_findings:
        lines.append(f"**{br.test_id}** {markdown_line(br.name or br.test_id)}\n")
        lines.extend(f"- {markdown_line(scrub_secrets(line))}" for line in br.findings)
        lines.append("")
    return "\n".join(lines).rstrip()


def raw_score_cell(br: TestResult) -> str:
    """A report-only table's score cell: the raw score, or "report only" for a findings-only result."""
    return "report only" if br.findings_only else f"{br.score:.1%}"


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
        "| Framework | Version | Inspected / mapped | Passing | Observed pass rate | Inspection coverage |",
        "|---|---|---|---|---|---|",
    ]

    for item in summary:
        lines.append(
            f"| {item['name']} | {item['version']} "
            f"| {item['tests_mapped']}/{item['tests_mapped_total']} | {item['tests_passing']} "
            f"| {item['coverage_pct']} | {item['inspection_coverage_pct']} |"
        )

    return "\n".join(lines)


def render_consistency_warnings(result: TestRunResult) -> str:
    if not result.validation_warnings:
        return ""
    lines = [
        "## Consistency Warnings\n",
        "> Cross-hook verification findings are listed below. Confirmed inconsistencies cap affected inspection scores at 50%; incomplete checks retain completed inspection scores.\n",
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
        lines.extend(findings_block(br))
        counts_errors = br.spec is not None and br.spec.count_extraction_errors_as_fail
        # With probes left ungraded, a coverage audit can't tell a gap in the agent from the cut.
        cut = any(ev.extraction_error in UNGRADED for ev in br.evidence)

        for ev in br.evidence:
            prompt_display = (
                ev.prompt_sent[:_PROMPT_DISPLAY_CAP] + "..."
                if len(ev.prompt_sent) > _PROMPT_DISPLAY_CAP
                else ev.prompt_sent
            )
            actual_display = ev.actual_response or ev.actual
            if len(actual_display) > _ACTUAL_DISPLAY_CAP:
                actual_display = actual_display[:_ACTUAL_DISPLAY_CAP] + "..."
            actual_line = f"`{actual_display}`"
            # Match the interactive scorecard: an ungraded item or an operator
            # diagnostic is not a failed answer, whatever its `passed` default; an item with
            # the fixture's own answers is a note.
            if ev.details.get("declared") is True:
                ev_status = "NOTE, INCLUDES THE FIXTURE'S ANSWERS"
            elif ev.is_diagnostic:
                ev_status = "DIAGNOSTIC"
            elif not counts_toward_score(ev, counts_errors) or (
                cut and not ev.passed and ev.test_case_id.endswith("-coverage-summary")
            ):
                # Items the score leaves out are not agent verdicts; an ungraded one says so. An
                # ungraded item the score counts as a fail (`count_extraction_errors_as_fail`)
                # shows as one.
                if ev.extraction_error is not None:
                    ev_status = "NOT JUDGED" if br.findings_only else "GRADING UNAVAILABLE"
                else:
                    ev_status = "UNSCORED"
            else:
                ev_status = "PASS" if ev.passed else "FAIL"
            if br.findings_only:
                # A transcript keeps its own lines.
                actual_line = actual_markdown(actual_display)
            judges = scrub_secrets(describe_votes(ev.details))

            lines.append(
                f"- **{ev.test_case_id}** [{ev_status}]: "
                f"{ev.description}\n"
                f"  - Prompt: `{prompt_display}`\n"
                f"  - Expected: {ev.expected or ev.expected_behavior}\n"
                f"  - Actual: {actual_line}\n"
                f"  - Evaluation: {ev.evaluation_result}"
                + (f"\n  - Judges: {judges}" if judges else "")
            )

        lines.append("")

    if not has_evidence:
        lines.append("No evidence items recorded.")

    return "\n".join(lines)
