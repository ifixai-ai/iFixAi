"""A run that scored no strategic inspection has no strategic score, as it has no overall score
when nothing gradable was scored: n/a, not 0.0%."""

import json

from ifixai.cli.compare import _parse_result
from ifixai.core.runner import _build_result
from ifixai.core.types import EvidenceItem, TestResult, TestStatus
from ifixai.harness.registry import SPEC_BY_ID
from ifixai.reporting.artifact import _build_payload
from ifixai.reporting.scorecard import (
    generate_json_report,
    render_insights,
    render_summary,
)
from ifixai.scoring.category_weights import STRATEGIC_TEST_IDS
from ifixai.scoring.engine import compute_strategic_score


def _result(test_id: str, score: float, status: TestStatus, insufficient: bool = False) -> TestResult:
    spec = SPEC_BY_ID[test_id]
    evidence = [
        EvidenceItem(test_case_id=f"{test_id}-{i}", description="d", expected="e", actual="a", passed=True)
        for i in range(10)
    ]
    return TestResult(
        test_id=test_id, spec=spec, name=spec.name, category=spec.category, score=score,
        threshold=spec.threshold, status=status, passed=status is TestStatus.PASS,
        passing=status is TestStatus.PASS, evidence=evidence, insufficient_evidence=insufficient,
    )


def _scoped_run_without_strategic_inspections():
    results = [_result("B12", 1.0, TestStatus.PASS), _result("B19", 1.0, TestStatus.PASS)]
    return _build_result(results, "sys", "1", "fx", "mock", "standard", selected_ids={"B12", "B19"})


def _run_whose_strategic_inspections_were_all_inconclusive():
    results = [_result(t, 1.0, TestStatus.PASS) for t in ("B08", "P01", "B12", "B19")]
    results += [
        _result(t, 0.0, TestStatus.INCONCLUSIVE, insufficient=True)
        for t in ("B02", "B03", "B04", "B05", "B06", "B07", "B25")
    ]
    b01 = _result("B01", 0.0, TestStatus.INCONCLUSIVE, insufficient=True)
    # The agent exposes no tool plane, so B01 is not applicable and the overall score stands.
    b01 = b01.model_copy(update={"evidence": [b01.evidence[0].model_copy(update={"test_case_id": "B01-capability-missing"})]})
    return _build_result([*results, b01], "sys", "1", "fx", "mock", "standard")


def test_no_strategic_score_when_none_was_selected_or_none_was_scored():
    assert compute_strategic_score([_result("B12", 1.0, TestStatus.PASS)], STRATEGIC_TEST_IDS) is None
    inconclusive = [_result("B02", 0.0, TestStatus.INCONCLUSIVE, insufficient=True)]
    assert compute_strategic_score(inconclusive, STRATEGIC_TEST_IDS) is None
    scored = [_result("B02", 1.0, TestStatus.PASS), _result("B03", 0.5, TestStatus.FAIL)]
    assert compute_strategic_score(scored, STRATEGIC_TEST_IDS) == 0.75


def test_a_scoped_run_reports_no_strategic_score_in_any_view():
    run = _scoped_run_without_strategic_inspections()
    assert run.strategic_score is None

    overall = json.loads(generate_json_report(run))["overall"]
    assert (overall["strategic_score"], overall["strategic_score_pct"]) == (None, "n/a")
    assert "| **Strategic Score** | n/a |" in render_summary(run)
    payload = _build_payload(
        run, live=False, transport="", sut_model=None, judge_model=None, honesty_note="", previous=None,
    )
    assert payload["summary"]["strategic_pct"] == "n/a"


def test_the_insights_do_not_compare_the_overall_score_with_a_strategic_score_that_was_not_measured():
    run = _run_whose_strategic_inspections_were_all_inconclusive()
    assert run.overall_score == 1.0
    assert run.strategic_score is None
    assert "Overall vs strategic" not in render_insights(run)


def test_compare_reads_a_missing_or_null_strategic_score_as_not_measured():
    raw = json.loads(generate_json_report(_scoped_run_without_strategic_inspections()))
    assert _parse_result(raw).strategic_score is None
    del raw["overall"]["strategic_score"]
    assert _parse_result(raw).strategic_score is None
