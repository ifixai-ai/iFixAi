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


def test_the_docs_page_indexes_the_spec_rather_than_copying_it() -> None:
    """One source per artefact.

    The specification prose is authored in the register, where a citation lands. This page is about
    the implementation and indexes the fields without restating the argument for each one — so there
    is no second copy to keep in step. A future edit that pastes the reference back in fails here.
    """
    page = (ROOT / "docs" / "register-spec.md").read_text(encoding="utf-8")
    assert "agent-misbehavior-registry/blob/main/spec/report-spec-1.md" in page
    assert "canonical specification" in page
    # The field-by-field reference lives in the register, not here.
    assert "## The required fields, and what breaks without each" not in page
    assert page.count("### ") <= 2, "this page is growing a second copy of the field reference"


def test_the_published_schema_can_be_checked_against_a_register() -> None:
    """`publish_register_spec.py --check` is what a maintainer runs before a release. It must fail
    loudly on a directory that is not a register, rather than appearing to succeed."""
    import subprocess
    import sys

    done = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "publish_register_spec.py"),
            "--registry",
            str(ROOT),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2
    assert "does not look like a clone of the register" in done.stderr


def test_the_worked_examples_are_tracked_by_git() -> None:
    """The examples are the test fixture, so an untracked one passes locally and fails in CI.

    It happened: they were first put in a top-level `examples/`, which this repository's .gitignore
    excludes, so `git add -A` silently skipped them and every fixture-backed test errored on the
    runner. They now live inside the package, where they are tracked and ship in the wheel — and this
    test fails rather than CI if anything moves them somewhere ignored again.
    """
    import subprocess

    paths = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "ifixai" / "register" / "examples").rglob("*.yaml")
    )
    assert paths, "the worked examples are missing"
    done = subprocess.run(
        ["git", "ls-files", "--", *paths],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:  # pragma: no cover - not a git checkout (an installed sdist)
        import pytest as _pytest

        _pytest.skip("not a git checkout")
    tracked = set(done.stdout.split())
    untracked = [path for path in paths if path not in tracked]
    assert not untracked, f"these example files are not tracked by git: {untracked}"
