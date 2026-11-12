"""Passive import of vendor trace exports into canonical trajectory captures.

Reads OpenTelemetry GenAI (OTLP JSON), Langfuse, LangSmith, Arize Phoenix / OpenInference and
Braintrust exports and writes the canonical JSONL capture (a header row, then one event per
row) that the trajectory audit loads. Only what a trace states is mapped: tool executions
become actions, retrievals become sources, a generation's final text becomes an output.
Approvals, revocations and delegations are never inferred; they come only from an explicit
`ifixai.event` carried by the trace. Every gap is returned as an import warning, and a capture
is complete only when the trace says so and the import found no gap.

    from ifixai.trace_intake import import_trace, write_result

    result = import_trace("export.json", tenant_id="acme", collector_id="otel-gw")
    write_result(result, "capture.jsonl")
"""

from ifixai.trace_intake._core import MAX_BYTES, MAX_EVENTS, TraceIntakeError
from ifixai.trace_intake.api import (
    SOURCES,
    ImportResult,
    detect_source,
    import_document,
    import_trace,
    warnings_path,
    write_result,
)
from ifixai.trace_intake.schema import load_capture_rows, validate_rows

__all__ = [
    "MAX_BYTES",
    "MAX_EVENTS",
    "SOURCES",
    "ImportResult",
    "TraceIntakeError",
    "detect_source",
    "import_document",
    "import_trace",
    "load_capture_rows",
    "validate_rows",
    "warnings_path",
    "write_result",
]
