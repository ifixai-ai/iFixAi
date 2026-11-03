"""Loading a public scorecard must retain the run's decision contract."""

import pytest

from ifixai.cli.compare import load_result_from_json
from ifixai.core.types import NotApplicable, TestRunResult, TestStatus
from ifixai.reporting.scorecard import generate_json_report, run_verdict


@pytest.mark.parametrize("kind", ["custom_mark", "selected", "set_aside", "inconclusive"])
def test_compare_loader_retains_decision_and_gate_context(tmp_path, kind):
    original = TestRunResult(overall_score=0.8, min_score=0.75,
                             mandatory_minimums_passed=True)
    if kind == "selected":
        original.mandatory_minimums_not_run = ["B01", "P01"]
        original.mandatory_minimum_status = {"B08": TestStatus.PASS}
    elif kind == "set_aside":
        original.not_applicable = [NotApplicable(test_id="P01", name="Destruction",
                                                 reason="No destructive tools", kind="agent")]
    elif kind == "inconclusive":
        original.mandatory_minimums_inconclusive = ["B08"]
        original.mandatory_minimum_status = {"B08": TestStatus.INCONCLUSIVE}
        original.mandatory_minimums_passed = False
    path = tmp_path / "scorecard.json"
    path.write_text(generate_json_report(original), encoding="utf-8")
    loaded = load_result_from_json(str(path))
    for field in ["min_score", "mandatory_minimum_status", "mandatory_minimums_not_run",
                  "mandatory_minimums_inconclusive", "not_applicable"]:
        assert getattr(loaded, field) == getattr(original, field), field
    assert run_verdict(loaded) == run_verdict(original)
