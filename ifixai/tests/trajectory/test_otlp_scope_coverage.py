"""Instrumentation-scope losses must survive normalization as evidence gaps."""
import json

import pytest

from ifixai.trajectory import audit_trajectory
from ifixai.trajectory.importers import load_otlp_capture


@pytest.mark.parametrize("scope", [
    {"name": "collector", "droppedAttributesCount": 1},
    {"name": "collector", "attributes": [{"key": "ifixai.unrecognized", "value": {"stringValue": "unknown semantic fact"}}]},
])
def test_otlp_scope_coverage_gap_cannot_be_clear(native_example, tmp_path, scope):
    capture, _, _ = native_example.record_scenario()
    raw = native_example.otlp_export(capture)
    raw["resourceSpans"][0]["scopeSpans"][0]["scope"] = scope
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    imported = load_otlp_capture(path)
    assert imported.events == capture.events
    assert imported.import_warnings
    assert audit_trajectory(imported, native_example.demo_policy()).decision == "INCONCLUSIVE"


def test_ordinary_scope_metadata_does_not_create_a_gap(native_example, tmp_path):
    capture, _, _ = native_example.record_scenario()
    raw = native_example.otlp_export(capture)
    raw["resourceSpans"][0]["scopeSpans"][0]["scope"] = {
        "name": "collector", "version": "1", "droppedAttributesCount": 0,
        "attributes": [{"key": "build", "value": {"stringValue": "local"}}],
    }
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    imported = load_otlp_capture(path)
    assert not imported.import_warnings
    assert audit_trajectory(imported, native_example.demo_policy()).decision == "CLEAR"


def test_unselected_trace_scope_loss_is_intentionally_excluded(native_example, tmp_path):
    selected, _, _ = native_example.record_scenario()
    other, _, _ = native_example.record_scenario()
    raw = native_example.otlp_export(selected)
    unselected = native_example.otlp_export(other)
    unselected["resourceSpans"][0]["scopeSpans"][0]["scope"] = {"droppedAttributesCount": 1}
    raw["resourceSpans"].extend(unselected["resourceSpans"])
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    imported = load_otlp_capture(path, trace_id=selected.trace_id)
    assert imported.events == selected.events
    assert not imported.import_warnings
    assert audit_trajectory(imported, native_example.demo_policy()).decision == "CLEAR"
