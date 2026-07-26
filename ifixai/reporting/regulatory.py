

from ifixai.core.types import RegulatoryFramework, TestRunResult
from ifixai.mappings.loader import get_mappings_for_test, load_all_mappings


def build_regulatory_summary(
    result: TestRunResult,
    frameworks: dict[str, RegulatoryFramework] | None = None,
) -> list[dict[str, object]]:
    if frameworks is None:
        frameworks = load_all_mappings()

    results_by_id = {br.test_id: br for br in result.test_results}
    summary: list[dict[str, object]] = []

    for fw_name, fw in frameworks.items():
        mapped_count = 0
        passing_count = 0

        for bid in fw.mappings:
            br = results_by_id.get(bid)
            if br is None:
                continue
            mapped_count += 1
            if br.passing:
                passing_count += 1

        coverage = passing_count / mapped_count if mapped_count > 0 else 0.0

        summary.append({
            "name": fw_name,
            "version": fw.version,
            "tests_mapped": mapped_count,
            "tests_passing": passing_count,
            "coverage": round(coverage, 4),
            "coverage_pct": f"{coverage:.0%}",
        })

    return summary

def build_regulatory_json_section(
    result: TestRunResult,
    frameworks: dict[str, RegulatoryFramework] | None = None,
) -> dict[str, object]:
    if frameworks is None:
        frameworks = load_all_mappings()

    return {
        "regulatory_frameworks": [
            {"name": fw.framework, "version": fw.version}
            for fw in frameworks.values()
        ],
        "compliance_summary": build_regulatory_summary(result, frameworks),
    }

def get_test_regulatory_mappings(
    test_id: str,
    frameworks: dict[str, RegulatoryFramework] | None = None,
) -> list[dict[str, str]]:
    mappings = get_mappings_for_test(test_id, frameworks)
    return [
        {
            "framework": m.framework,
            "framework_version": m.framework_version,
            "control_id": m.control_id,
            "control_name": m.control_name,
            "relevance": m.relevance,
        }
        for m in mappings
    ]
