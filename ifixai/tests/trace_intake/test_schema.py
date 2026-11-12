from __future__ import annotations

import copy
import json

import pytest

from ifixai.trace_intake import TraceIntakeError, load_capture_rows, validate_rows
from ifixai.trace_intake.schema import validate_payload

TRACE = "0123456789abcdef0123456789abcdef"
DIGEST = "0" * 64


def event(event_id, sequence, payload, parents=(), time_ns=None):
    return {"event_id": event_id, "trace_id": TRACE, "tenant_id": "t", "sequence": sequence,
            "time_ns": sequence if time_ns is None else time_ns, "parents": list(parents),
            "payload": payload}


def good_rows():
    return [
        {"schema_version": "1", "trace_id": TRACE, "tenant_id": "t", "collector_id": "c",
         "capture_complete": False, "source_format": "jsonl"},
        event("s", 1, {"kind": "start"}),
        event("a", 2, {"kind": "action", "actor_id": "agent", "action": "x", "resource": "tool:x",
                       "arguments_digest": DIGEST, "outcome": "unknown"}, ["s"]),
        event("e", 3, {"kind": "end", "complete": False, "declared_events": 3}, ["a"]),
    ]


def test_good_rows_pass(tmp_path):
    validate_rows(good_rows())
    path = tmp_path / "c.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in good_rows()))
    assert len(load_capture_rows(path)) == 4


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r[0].update(source_format="otlp"), "invalid capture row"),
        (lambda r: r[0].update(import_warnings=[]), "invalid capture row"),
        (lambda r: r[0].update(trace_id="ABC"), "invalid capture row"),
        (lambda r: r[2].update(sequence=5), "contiguous"),
        (lambda r: r[2].update(event_id="s"), "duplicated"),
        (lambda r: r[2].update(time_ns=0), "decreases"),
        (lambda r: r[2].update(parents=["e"]), "precede"),
        (lambda r: r[2].update(parents=["s", "s"]), "repeat"),
        (lambda r: r[2].update(parents=[]), "no parents"),
        (lambda r: r[2].update(tenant_id="other"), "tenant"),
        (lambda r: r[2]["payload"].update(resource="tool:*"), "invalid capture row"),
        (lambda r: r[2]["payload"].update(outcome="succeeded"), "invalid capture row"),
        (lambda r: r[3]["payload"].update(declared_events=4), "declared_events"),
        (lambda r: r[3]["payload"].update(complete=True), "capture_complete"),
        (lambda r: r[1].update(payload={"kind": "output", "actor_id": "a"}), "start"),
        (lambda r: r[3].update(payload={"kind": "output", "actor_id": "a"}), "end"),
        (lambda r: r[1].update(parents=["x"]), "precede"),
        (lambda r: r[2].update(sequence=True), "invalid capture row"),
    ],
)
def test_bad_rows_refused(mutate, message):
    rows = copy.deepcopy(good_rows())
    mutate(rows)
    with pytest.raises(TraceIntakeError, match=message):
        validate_rows(rows)


def test_structural_minimums():
    with pytest.raises(TraceIntakeError, match="header"):
        validate_rows([])
    with pytest.raises(TraceIntakeError, match="at least"):
        validate_rows(good_rows()[:2])


def test_validate_payload_normalizes_and_refuses():
    assert validate_payload({"kind": "start"}) == {"kind": "start", "label": ""}
    with pytest.raises(TraceIntakeError, match="invalid canonical payload"):
        validate_payload({"kind": "approval", "actor_id": "m"})
    with pytest.raises(TraceIntakeError):
        validate_payload({"kind": "delegation", "actor_id": "a", "subject_id": "b", "action": "x",
                          "resource_scope": "a*b", "expires_ns": 1})
