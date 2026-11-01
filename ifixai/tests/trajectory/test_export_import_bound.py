"""A portable native export must remain inside its own import byte contract."""
import pytest

from ifixai.trajectory import importers
from ifixai.trajectory.recorder import TraceRecorder, write_capture_jsonl


def test_oversized_native_export_refused_before_creating_evidence(tmp_path, monkeypatch):
    capture = TraceRecorder("tenant", "collector", label="é" * 500, clock_ns=lambda: 10).seal()
    destination = tmp_path / "capture.jsonl"
    monkeypatch.setattr(importers, "MAX_BYTES", 1000)
    with pytest.raises(ValueError, match=r"import.*limit"):
        write_capture_jsonl(capture, destination)
    assert not destination.exists()
    assert not list(tmp_path.iterdir())


def test_native_export_at_exact_byte_limit_round_trips(tmp_path, monkeypatch):
    capture = TraceRecorder("tenant", "collector", label="é" * 500, clock_ns=lambda: 10).seal()
    original = tmp_path / "original.jsonl"
    write_capture_jsonl(capture, original)
    monkeypatch.setattr(importers, "MAX_BYTES", len(original.read_bytes()))
    destination = tmp_path / "capture.jsonl"
    write_capture_jsonl(capture, destination)
    assert importers.load_jsonl_capture(destination).events == capture.events
