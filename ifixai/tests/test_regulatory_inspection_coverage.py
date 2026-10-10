import json

from ifixai.core.types import TestResult, TestRunResult, TestStatus
from ifixai.mappings.loader import load_all_mappings
from ifixai.reporting.regulatory import build_regulatory_summary
from ifixai.reporting.scorecard import (
    generate_json_report,
    render_regulatory_compliance,
)


def test_partial_scorecard_separates_observed_pass_rate_from_inspection_coverage():
    result = TestRunResult(test_results=[TestResult(test_id='B01', passing=True, status=TestStatus.PASS, score=1.0)])
    frameworks = load_all_mappings()
    rows = build_regulatory_summary(result, frameworks)
    export = json.loads(generate_json_report(result))['regulatory']['compliance_summary']
    for row, exported in zip(rows, export, strict=True):
        total = len(frameworks[row['name']].mappings)
        assert total > 1
        assert row['tests_mapped'] == 1
        assert row['coverage'] == 1.0  # legacy observed pass-rate field retained
        assert row['tests_mapped_total'] == total
        assert row['tests_not_run'] == total - 1
        assert row['inspection_coverage'] == round(1 / total, 4)
        assert exported['tests_mapped_total'] == total
    markdown = render_regulatory_compliance(result, frameworks)
    assert 'Inspected / mapped' in markdown
    assert 'Observed pass rate' in markdown
    assert 'Inspection coverage' in markdown


def test_full_and_empty_runs_keep_mapping_denominator():
    frameworks = load_all_mappings()
    ids = sorted({test_id for fw in frameworks.values() for test_id in fw.mappings})
    full = TestRunResult(test_results=[TestResult(test_id=test_id, passing=True, status=TestStatus.PASS, score=1.0) for test_id in ids])
    for row in build_regulatory_summary(full, frameworks):
        assert row['tests_mapped'] == row['tests_mapped_total']
        assert row['tests_not_run'] == 0
        assert row['coverage'] == row['inspection_coverage'] == 1.0
    for row in build_regulatory_summary(TestRunResult(), frameworks):
        assert row['tests_mapped'] == 0
        assert row['tests_not_run'] == row['tests_mapped_total']
        assert row['coverage'] == row['inspection_coverage'] == 0.0


def test_artifact_labels_observed_pass_rate_and_inspection_coverage():
    from ifixai.reporting.artifact import render_artifact
    html = render_artifact(TestRunResult(test_results=[TestResult(test_id='B01', passing=True, status=TestStatus.PASS, score=1.0)]), live=False, transport='owned fixture', sut_model=None, judge_model=None, honesty_note='Offline rendering control')
    assert '<th>Inspected / mapped</th>' in html
    assert '<th>Observed pass rate</th>' in html
    assert '<th>Inspection coverage</th>' in html
