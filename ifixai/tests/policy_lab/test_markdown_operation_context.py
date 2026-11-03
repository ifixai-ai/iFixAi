"""Shareable Markdown must retain the operation and its actual outcome."""

from ifixai.policy_lab import create_exploration_bundle
from ifixai.policy_lab.repair import search_repairs
from ifixai.reporting.policy_lab import compare_policies, write_policy_lab_reports


def test_markdown_repair_reports_selection_and_goal_preservation(native_example, tmp_path):
    bundle = create_exploration_bundle(native_example.demo_model(), native_example.demo_policy())
    repair = search_repairs(bundle)
    write_policy_lab_reports(bundle, tmp_path / "repair", repair=repair)
    markdown = (tmp_path / "repair" / "report.md").read_text()
    assert "Repair: **FOUND**" in markdown
    assert "Baseline decision: **COUNTEREXAMPLE**" in markdown
    assert "Selected policy decision: **VERIFIED**" in markdown
    assert "legitimate\\_refund: REACHABLE" in markdown
    assert "Minimum removal cardinality proven: true" in markdown
    assert "(1,)" in markdown
    assert "selected-bundle.json" in markdown


def test_markdown_comparison_keeps_baseline_candidate_and_objective_changes(native_example, tmp_path):
    original = create_exploration_bundle(native_example.demo_model(), native_example.demo_policy())
    candidate = search_repairs(original).selected_bundle
    assert candidate is not None
    comparison = compare_policies(original, candidate)
    write_policy_lab_reports(candidate, tmp_path / "compare", comparison=comparison)
    markdown = (tmp_path / "compare" / "report.md").read_text()
    assert "## Policy comparison" in markdown
    assert "COUNTEREXAMPLE → VERIFIED" in markdown
    assert original.integrity_digest in markdown
    assert "legitimate\\_refund: REACHABLE → REACHABLE" in markdown


def test_markdown_inconclusive_repair_retains_cutoff_reason(native_example, tmp_path):
    bundle = create_exploration_bundle(native_example.demo_model(), native_example.demo_policy())
    repair = search_repairs(bundle, max_candidates=1)
    write_policy_lab_reports(bundle, tmp_path / "limited", repair=repair)
    markdown = (tmp_path / "limited" / "report.md").read_text()
    assert "Repair: **INCONCLUSIVE**" in markdown
    assert "REPAIR\\_CANDIDATE\\_LIMIT" in markdown
    assert "Selected policy decision" not in markdown
