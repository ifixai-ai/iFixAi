"""Pending observations own capacity before the application can mutate state."""
import threading

import pytest

from ifixai.trajectory.models import SourcePayload
from ifixai.trajectory.recorder import TraceRecorder


def test_pending_call_reserves_record_capacity_before_another_side_effect():
    trace = TraceRecorder("tenant", "collector", clock_ns=lambda: 10)
    source = SourcePayload(actor_id="actor", resource="local:source", content_digest="a" * 64)
    while len(trace._events) < 99998:
        trace.emit(source)
    entered, release = threading.Event(), threading.Event()
    errors = []
    calls = []

    def pending():
        entered.set()
        assert release.wait(10)
        calls.append("first")

    def observe():
        try:
            trace.invoke(pending, actor_id="actor", action="write", resource="local:one", arguments={})
        except (ValueError, AssertionError) as error:
            errors.append(error)

    worker = threading.Thread(target=observe)
    worker.start()
    try:
        assert entered.wait(10)
        with pytest.raises(ValueError, match="event bound"):
            trace.invoke(lambda: calls.append("second"), actor_id="actor", action="write", resource="local:two", arguments={})
        with pytest.raises(ValueError, match="event bound"):
            trace.emit(source)
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    assert not errors
    assert calls == ["first"]
    capture = trace.seal()
    assert len(capture.events) == 100000
    assert capture.capture_complete
    assert capture.events[-2].payload.kind == "action"
