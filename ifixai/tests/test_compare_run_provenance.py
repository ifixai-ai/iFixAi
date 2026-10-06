"""Public JSON exports retain provenance when the compare CLI loads them."""

from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from ifixai.cli.compare import compare, load_result_from_json
from ifixai.core.runner import build_partial_result
from ifixai.core.types import TestGrade, TestResult, TestRunResult, TestStatus
from ifixai.reporting.scorecard import (
    generate_json_report,
    render_partial_banner,
    render_resumed_banner,
)


def exported_run(kind):
    completed = TestResult(
        test_id="B01", name="Tool Invocation Governance", score=0.8,
        status=TestStatus.PASS, passed=True, passing=True,
    )
    result = TestRunResult(
        system_name="owned-run", test_results=[completed],
        overall_score=0.8, grade=TestGrade.B,
    )
    if kind in {"partial", "both"}:
        result = build_partial_result(
            completed=[completed], planned_ids=["B01", "B06"],
            abort_reason="owned cancellation", system_name="owned-run",
            system_version="1.0", fixture_name="default", provider_name="mock",
            run_mode="selected",
        )
    if kind in {"resumed", "both"}:
        result.resumed_run_id = "owned-session"
        result.reused_result_count = 1
    return result


@pytest.mark.parametrize("kind", ["partial", "resumed", "both", "complete"])
@pytest.mark.parametrize("side", ["baseline", "enhanced"])
def test_compare_displays_existing_exported_provenance_banners(tmp_path, kind, side):
    result = exported_run(kind)
    ordinary = exported_run("complete")
    paths = {}
    for name in ["baseline", "enhanced"]:
        path = tmp_path / f"{name}.json"
        path.write_text(generate_json_report(result if name == side else ordinary), encoding="utf-8")
        paths[name] = str(path)
    cli = CliRunner().invoke(compare, [paths["baseline"], paths["enhanced"]])
    assert cli.exit_code == 0, cli.exception
    for banner in [render_partial_banner(result), render_resumed_banner(result)]:
        if banner:
            assert f"{side.title()}: {banner}" in cli.output
    if kind == "complete":
        assert "PARTIAL RUN" not in cli.output
        assert "Resumed run" not in cli.output
        assert "Delta:    +0.0%" in cli.output
    else:
        loaded = load_result_from_json(paths[side])
        for field in ["partial", "abort_reason", "not_run_test_ids", "resumed_run_id", "reused_result_count"]:
            assert getattr(loaded, field) == getattr(result, field)


def test_loader_preserves_exported_provenance_without_changing_scores(tmp_path):
    result = exported_run("both")
    result.evaluation_date = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    result.specification_version = "3.0-owned"
    result.self_judged = True
    result.judge_relation = "same-provider"
    result.judge_stats = {"calls": 2, "cache_hits": 1}
    result.warnings.append("owned grading warning")
    result.validation_warnings = ["owned fixture warning"]
    path = tmp_path / "export.json"
    path.write_text(generate_json_report(result), encoding="utf-8")
    loaded = load_result_from_json(str(path))
    for field in [
        "evaluation_date", "specification_version", "self_judged", "judge_relation",
        "judge_stats", "warnings", "validation_warnings", "partial", "abort_reason",
        "not_run_test_ids", "resumed_run_id", "reused_result_count",
    ]:
        assert getattr(loaded, field) == getattr(result, field)
    assert loaded.overall_score == result.overall_score
    assert loaded.grade is result.grade
    assert loaded.test_results[0].score == result.test_results[0].score
