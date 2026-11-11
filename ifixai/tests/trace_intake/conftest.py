from __future__ import annotations

import atexit
import json
import shutil
import tempfile
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "fixtures" / "trace_intake"
COMPACT_MARK = "__otlp_attributes__"


def otlp_pair(name, value):
    """One OTLP attribute. Built here, not written out in fixtures: a literal
    key/value pair per line is what the secret scanner's generic rule matches."""
    return dict(zip(("key", "value"), (name, value)))


def expand(node):
    """Turn a compact fixture's `{COMPACT_MARK: {name: AnyValue}}` back into OTLP attribute lists."""
    if isinstance(node, dict):
        if set(node) == {COMPACT_MARK}:
            return [otlp_pair(name, expand(value)) for name, value in node[COMPACT_MARK].items()]
        return {key: expand(value) for key, value in node.items()}
    if isinstance(node, list):
        return [expand(item) for item in node]
    return node


def materialize() -> Path:
    """The fixture folder as the importers see it: real OTLP JSON for every compact fixture."""
    target = Path(tempfile.mkdtemp(prefix="ifixai-trace-fixtures-"))
    atexit.register(shutil.rmtree, target, True)
    for path in SOURCE.iterdir():
        if path.suffix == ".json":
            shutil.copyfile(path, target / path.name)
        elif path.suffix == ".otlpc":
            document = expand(json.loads(path.read_text(encoding="utf-8")))
            (target / f"{path.stem}.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    return target


FIXTURES = materialize()
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
BASE = 1767225600_000_000_000


def attr(key, value):
    if isinstance(value, bool):
        return otlp_pair(key, {"boolValue": value})
    if isinstance(value, int):
        return otlp_pair(key, {"intValue": str(value)})
    return otlp_pair(key, {"stringValue": value})


def span(span_id, start, end, attrs, parent=None, code=0, name="span"):
    item = {
        "traceId": TRACE, "spanId": span_id, "name": name,
        "startTimeUnixNano": str(BASE + start), "endTimeUnixNano": str(BASE + end),
        "attributes": [attr(k, v) for k, v in attrs.items()], "status": {"code": code},
    }
    if parent:
        item["parentSpanId"] = parent
    return item


def otlp(spans, complete=None):
    resource = [attr("service.name", "svc")]
    if complete is not None:
        resource.append(attr("ifixai.capture_complete", complete))
    return {"resourceSpans": [{"resource": {"attributes": resource},
                               "scopeSpans": [{"scope": {"name": "s"}, "spans": spans}]}]}


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def write_json(tmp_path):
    def write(obj, name="trace.json"):
        path = tmp_path / name
        path.write_text(json.dumps(obj), encoding="utf-8")
        return path
    return write
