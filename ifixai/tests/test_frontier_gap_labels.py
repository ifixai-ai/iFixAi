"""Current inspection families must retain actionable names in gap reports."""

import pytest

from ifixai.core.types import TestResult, TestStatus
from ifixai.harness.registry import ALL_SPECS
from ifixai.reporting.gap_analysis import identify_gaps


@pytest.mark.parametrize("test_id", ["B30", "P01", "M01", "S01", "V01"])
def test_shipped_family_failure_has_named_gap_in_json(test_id):
    spec = next(spec for spec in ALL_SPECS if spec.test_id == test_id)
    failure = TestResult(test_id=test_id, name=spec.name, category=spec.category,
                         score=0, threshold=spec.threshold, status=TestStatus.FAIL)
    gap = identify_gaps([failure], frameworks={})[0].model_dump(mode="json")
    assert gap["capability_missing"] == spec.name
    assert gap["gap_description"].startswith(spec.name + " — scored")
    assert "Unknown Capability" not in gap["gap_description"]


def test_known_capability_keeps_existing_label():
    failure = TestResult(test_id="B01", name="Custom display title", status=TestStatus.FAIL)
    assert identify_gaps([failure], frameworks={})[0].capability_missing == "Tool Authorization Control"


def test_unnamed_custom_inspection_keeps_identifier_fallback():
    failure = TestResult(test_id="X99", status=TestStatus.FAIL)
    assert identify_gaps([failure], frameworks={})[0].capability_missing == "Unknown Capability (X99)"
