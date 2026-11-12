"""The published JSON Schema is the model's own, and it accepts and rejects what the model does.

The schema in the repository is generated from ``ifixai/register/spec.py``. Two statements of one
format drift, and the one outside submitters validate against must be the one the library enforces,
so CI regenerates it and fails on a difference. These tests are the same check, plus a round trip
through ``jsonschema`` so the file is known to work for somebody who never installs iFixAi.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ifixai.register.spec import SPEC_VERSION, MisbehaviourReport

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "ifixai" / "schemas" / f"misbehaviour-report-{SPEC_VERSION}.schema.json"


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_the_schema_file_exists_and_is_versioned() -> None:
    assert SCHEMA_PATH.exists()
    assert SPEC_VERSION in SCHEMA_PATH.name


def test_the_schema_matches_the_model() -> None:
    """The same check CI runs. If this fails, run `python scripts/generate_register_schema.py`."""
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_register_schema.py"), "--check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_schema_says_what_it_is(schema: dict) -> None:
    assert "runtime-neutral" in schema["description"].lower()
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert SPEC_VERSION in schema["title"]


def test_the_schema_accepts_a_valid_report(schema: dict, entry: MisbehaviourReport) -> None:
    import jsonschema

    jsonschema.validate(entry.model_dump(mode="json"), schema)


def test_the_schema_rejects_a_report_missing_a_required_section(
    schema: dict, entry: MisbehaviourReport
) -> None:
    import jsonschema

    payload = entry.model_dump(mode="json")
    del payload["twin"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)


def test_the_schema_offers_no_llm_judge_grading_source(schema: dict) -> None:
    """A check whose value depends on a model's opinion is not reproducible, so the enum has two
    members and a third would be a different spec."""
    sources = schema["$defs"]["GradingCheck"]["properties"]["source"]["enum"]
    assert sorted(sources) == ["evidence_log", "world_state"]


def test_the_schema_fixes_identity_to_the_login(schema: dict) -> None:
    identity = schema["$defs"]["Identity"]["properties"]["identity_source"]
    assert identity.get("const") == "login" or identity.get("enum") == ["login"]
