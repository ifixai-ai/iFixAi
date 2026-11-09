"""Repair exports must belong to their baseline and computed search."""

import pytest

from ifixai.policy_lab import create_exploration_bundle
from ifixai.policy_lab.repair import search_repairs
from ifixai.reporting.policy_lab import write_policy_lab_reports


@pytest.mark.parametrize("mutation", ["baseline", "summary", "selected", "minimality"])
def test_repair_export_rejects_unbound_or_changed_result(native_example, tmp_path, mutation):
    bundle = create_exploration_bundle(native_example.demo_model(), native_example.demo_policy())
    repair = search_repairs(bundle)
    assert repair.status == "FOUND"
    if mutation == "baseline":
        other_model = bundle.model.model_copy(update={"name": "Different baseline"})
        bundle = create_exploration_bundle(other_model, bundle.policy)
    elif mutation == "summary":
        repair.candidates[0].report.goal_status["legitimate_refund"] = "REACHABLE"
    elif mutation == "selected":
        repair = repair.model_copy(update={"selected_bundle": bundle})
    else:
        repair = repair.model_copy(update={"minimality_proven": False})
    destination = tmp_path / "repair"
    with pytest.raises(ValueError):
        write_policy_lab_reports(bundle, destination, repair=repair)
    assert not destination.exists()
