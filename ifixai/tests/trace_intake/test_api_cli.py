"""Auto-detection, file writing and the `ifixai traces import` command."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli
from ifixai.trace_intake import (
    TraceIntakeError,
    _core,
    detect_source,
    import_document,
    import_trace,
    load_capture_rows,
    warnings_path,
    write_result,
)

SOURCES = ("otel", "langfuse", "langsmith", "arize", "braintrust")


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("case", ["clean", "failed_tool", "approve_refund"])
def test_auto_detect_every_fixture(fixtures, source, case):
    raw = json.loads((fixtures / f"{source}_{case}.json").read_text())
    assert detect_source(raw) == source


def test_detect_refuses_unknown_shapes():
    for raw in ({"hello": 1}, [], [1], "x"):
        with pytest.raises(TraceIntakeError, match="cannot detect"):
            detect_source(raw)


def test_options_are_validated(fixtures):
    for kwargs in ({"tenant_id": " t"}, {"collector_id": ""}, {"max_text": 5000}, {"max_text": True}):
        options = {"tenant_id": "t", "collector_id": "c", **kwargs}
        with pytest.raises(TraceIntakeError):
            import_trace(fixtures / "otel_clean.json", **options)
    with pytest.raises(TraceIntakeError, match="unknown source"):
        import_document({}, "splunk", tenant_id="t", collector_id="c")


def test_jsonl_input_is_accepted(fixtures, tmp_path):
    rows = json.loads((fixtures / "braintrust_clean.json").read_text())["events"]
    path = tmp_path / "bt.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert import_trace(path, tenant_id="t", collector_id="c").source == "braintrust"


def test_oversized_input_refused(tmp_path, monkeypatch):
    path = tmp_path / "big.json"
    path.write_text('{"resourceSpans": []}' + " " * 64)
    monkeypatch.setattr(_core, "MAX_BYTES", 32)
    with pytest.raises(TraceIntakeError, match="64 MiB"):
        import_trace(path, tenant_id="t", collector_id="c")


def test_oversized_output_refused(fixtures, monkeypatch):
    from ifixai.trace_intake import api

    monkeypatch.setattr(api, "MAX_BYTES", 100)
    with pytest.raises(TraceIntakeError, match="exceed"):
        import_trace(fixtures / "otel_clean.json", tenant_id="t", collector_id="c")


def test_write_result_round_trips_and_refuses_overwrite(fixtures, tmp_path):
    result = import_trace(fixtures / "otel_clean.json", tenant_id="t", collector_id="c")
    out = tmp_path / "capture.jsonl"
    capture_path, sidecar = write_result(result, out)
    assert sidecar == warnings_path(out) == tmp_path / "capture.jsonl.warnings.json"
    assert load_capture_rows(capture_path) == result.capture_rows
    document = json.loads(sidecar.read_text())
    assert document["warnings"] == result.warnings
    assert document["capture_complete"] is False
    with pytest.raises(TraceIntakeError, match="overwrite"):
        write_result(result, out)


def test_cli_import(fixtures, tmp_path):
    out = tmp_path / "out.jsonl"
    runner = CliRunner()
    outcome = runner.invoke(ifixai_cli, [
        "traces", "import", str(fixtures / "langsmith_clean.json"), "--from", "langsmith",
        "--tenant", "acme", "--collector", "ls-export", "-o", str(out), "--max-text", "10",
    ])
    assert outcome.exit_code == 0, outcome.output
    assert "Imported 5 events from langsmith" in outcome.output
    assert "incomplete" in outcome.output
    rows = load_capture_rows(out)
    assert rows[0]["collector_id"] == "ls-export"
    assert (tmp_path / "out.jsonl.warnings.json").exists()


def test_cli_auto_and_errors(fixtures, tmp_path):
    runner = CliRunner()
    out = tmp_path / "a.jsonl"
    ok = runner.invoke(ifixai_cli, ["traces", "import", str(fixtures / "otel_clean.json"),
                                    "--tenant", "t", "--collector", "c", "-o", str(out)])
    assert ok.exit_code == 0 and "from otel" in ok.output
    again = runner.invoke(ifixai_cli, ["traces", "import", str(fixtures / "otel_clean.json"),
                                       "--tenant", "t", "--collector", "c", "-o", str(out)])
    assert again.exit_code != 0 and "overwrite" in again.output
    bad = runner.invoke(ifixai_cli, ["traces", "import", str(fixtures / "otel_two_traces.json"),
                                     "--tenant", "t", "--collector", "c",
                                     "-o", str(tmp_path / "b.jsonl")])
    assert bad.exit_code != 0 and "--trace-id" in bad.output
    picked = runner.invoke(ifixai_cli, [
        "traces", "import", str(fixtures / "otel_two_traces.json"), "--tenant", "t",
        "--collector", "c", "--trace-id", "0af7651916cd43dd8448eb211c80319c",
        "-o", str(tmp_path / "c.jsonl")])
    assert picked.exit_code == 0
    missing_opt = runner.invoke(ifixai_cli, ["traces", "import", str(fixtures / "otel_clean.json"),
                                             "-o", str(tmp_path / "d.jsonl")])
    assert missing_opt.exit_code != 0
