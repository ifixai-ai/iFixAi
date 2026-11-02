"""A baseline is a paired experiment, not just a reused profile label."""
from dataclasses import replace

import pytest

from ifixai.windtunnel.defects import CATALOGUE
from ifixai.windtunnel.evidence import compare_to_baseline, create_bundle
from ifixai.windtunnel.measure import InspectionOutcome, WindTunnelReport


def _bundle(profile):
    report = WindTunnelReport(profile.name, (InspectionOutcome("B01", "detected", 1.0, 0.0, 1.0, True),))
    return create_bundle(((profile, report),), "customer_support")


@pytest.mark.parametrize("change", ["seed", "rate", "tools", "roles", "expected_detectors"])
def test_same_name_different_experiment_is_not_comparable(change):
    profile = CATALOGUE["over_permissive"]
    if change == "seed":
        changed = replace(profile, seed=7)
    elif change == "expected_detectors":
        changed = replace(profile, expected_detectors=frozenset({"B01"}))
    else:
        value = 0.1 if change == "rate" else frozenset({"restricted"})
        changed = replace(profile, defects=(replace(profile.defects[0], **{change: value}),))
    with pytest.raises(ValueError, match=r"profile.*changed"):
        compare_to_baseline(_bundle(profile), _bundle(changed))


def test_description_edit_does_not_change_experiment():
    profile = CATALOGUE["over_permissive"]
    result = compare_to_baseline(_bundle(profile), _bundle(replace(profile, description="clarified")))
    assert not result.regressed and not result.unmatched
