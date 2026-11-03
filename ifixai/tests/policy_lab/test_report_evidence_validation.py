"""Nested mappings remain mutable even on frozen evidence models."""

import pytest

from ifixai.policy_lab import create_exploration_bundle
from ifixai.reporting.policy_lab import compare_policies, write_policy_lab_reports


@pytest.mark.parametrize("mutation", ["goal", "model", "resigned"])
def test_report_export_rejects_changed_evidence_before_creating_output(native_example, tmp_path, mutation):
    bundle = create_exploration_bundle(native_example.demo_model(), native_example.demo_policy())
    if mutation == "model":
        bundle.model.initial_facts["paid"] = True
    else:
        bundle.report.goal_status["legitimate_refund"] = "UNREACHABLE"
    if mutation == "resigned":
        from ifixai.policy_lab import canonical_digest

        payload = bundle.model_dump(mode="json")
        payload.pop("integrity_digest")
        bundle = bundle.model_copy(update={"integrity_digest": canonical_digest(payload)})
    destination = tmp_path / "reports"
    with pytest.raises(ValueError):
        write_policy_lab_reports(bundle, destination)
    assert not destination.exists()


@pytest.mark.parametrize("side", ["original", "candidate"])
def test_comparison_revalidates_actual_inputs_not_just_claimed_digests(native_example, side):
    original = create_exploration_bundle(native_example.demo_model(), native_example.demo_policy())
    candidate = create_exploration_bundle(native_example.demo_model(), native_example.demo_policy())
    bundle = original if side == "original" else candidate
    bundle.report.goal_status["legitimate_refund"] = "UNREACHABLE"
    with pytest.raises(ValueError):
        compare_policies(original, candidate)
