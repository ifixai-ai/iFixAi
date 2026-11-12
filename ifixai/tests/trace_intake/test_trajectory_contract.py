"""Every fixture's output must load with the canonical trajectory loader.

`ifixai.trajectory` lands with a later OSS sync; until then this contract is skipped and the
package's own strict validation (test_schema.py) carries the structural checks.
"""

from __future__ import annotations

import pytest

from ifixai.trace_intake import import_trace, write_result

from .conftest import FIXTURES

try:
    from ifixai.trajectory.importers import load_jsonl_capture
except ImportError:  # pragma: no cover - depends on the OSS sync
    load_jsonl_capture = None

pytestmark = pytest.mark.skipif(
    load_jsonl_capture is None,
    reason="ifixai.trajectory is not on this branch yet (arrives with the OSS sync)",
)

IMPORTABLE = sorted(
    path for path in FIXTURES.glob("*.json")
    if path.name not in ("otel_malformed.json", "otel_two_traces.json")
)


@pytest.mark.parametrize("path", IMPORTABLE, ids=lambda path: path.stem)
def test_fixture_round_trips_through_load_jsonl_capture(path, tmp_path):
    result = import_trace(path, tenant_id="acme", collector_id="col-1")
    out, _ = write_result(result, tmp_path / "capture.jsonl")
    capture = load_jsonl_capture(out)
    assert capture.trace_id == result.header["trace_id"]
    assert capture.capture_complete is result.header["capture_complete"]
    assert capture.source_format == "jsonl"
    assert [event.model_dump(mode="json") for event in capture.events] == result.events
