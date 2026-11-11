from __future__ import annotations

import pytest

from ifixai.trace_intake import _core
from ifixai.trace_intake._core import (
    TraceIntakeError,
    Warnings,
    cap_text,
    has_truncation_marker,
    iso_to_ns,
    parse_document,
    read_bounded,
    seconds_to_ns,
    strict_json_loads,
)


def test_strict_json_refuses_duplicates_and_nonfinite():
    with pytest.raises(TraceIntakeError):
        strict_json_loads('{"a": 1, "a": 2}')
    with pytest.raises(TraceIntakeError):
        strict_json_loads("[NaN]")
    with pytest.raises(TraceIntakeError):
        strict_json_loads("[1e999]")


def test_parse_document_reads_json_lines_and_refuses_garbage():
    assert parse_document('{"a": 1}\n\n{"b": 2}\n') == [{"a": 1}, {"b": 2}]
    with pytest.raises(TraceIntakeError, match="neither JSON"):
        parse_document('{"a": 1}\nnot json\n')
    with pytest.raises(TraceIntakeError, match="empty"):
        parse_document("   \n")


def test_parse_document_record_cap(monkeypatch):
    monkeypatch.setattr(_core, "MAX_EVENTS", 2)
    with pytest.raises(TraceIntakeError, match="record limit"):
        parse_document("{}\n{}\n{}\n")


def test_read_bounded_refuses_oversize_and_non_utf8(tmp_path, monkeypatch):
    path = tmp_path / "big.json"
    path.write_bytes(b"x" * 11)
    monkeypatch.setattr(_core, "MAX_BYTES", 10)
    with pytest.raises(TraceIntakeError, match="64 MiB"):
        read_bounded(path)
    monkeypatch.undo()
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(TraceIntakeError, match="UTF-8"):
        read_bounded(path)


def test_iso_timestamps():
    assert iso_to_ns("1970-01-01T00:00:01Z") == 10**9
    assert iso_to_ns("1970-01-01T00:00:00.000000001Z") == 1
    assert iso_to_ns("1970-01-01T01:00:00+01:00") == 0
    assert iso_to_ns("1970-01-01T00:00:00-0030") == 30 * 60 * 10**9
    assert iso_to_ns("1970-01-01 00:00:02") == 2 * 10**9
    assert iso_to_ns("yesterday") is None
    assert iso_to_ns("2026-02-30T00:00:00Z") is None
    assert iso_to_ns(12) is None
    assert iso_to_ns("1960-01-01T00:00:00Z") is None


def test_seconds_to_ns():
    assert seconds_to_ns(1.5) == 1_500_000_000
    assert seconds_to_ns("2") == 2 * 10**9
    assert seconds_to_ns(True) is None
    assert seconds_to_ns("soon") is None
    assert seconds_to_ns(-1) is None


def test_cap_text_and_truncation_markers():
    assert cap_text("  hello world ", 5) == "hello"
    assert cap_text("x" * 5000, 5000) == "x" * 1000
    assert has_truncation_marker("body ... [truncated]")
    assert has_truncation_marker({"truncated": True})
    assert has_truncation_marker([{"a": "(redacted)"}])
    assert not has_truncation_marker({"a": "plain"})
    deep = "x"
    for _ in range(40):
        deep = [deep]
    assert not has_truncation_marker(deep)


def test_warnings_aggregate():
    warnings = Warnings()
    assert not warnings
    warnings.add("a", "first", "span-1")
    warnings.add("a", "first", "span-2")
    warnings.add("b", "second")
    assert "a" in warnings
    assert warnings.render() == ["[a] first (2 occurrences); first at span-1", "[b] second"]
