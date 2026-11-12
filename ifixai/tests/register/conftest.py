"""Fixtures for the register tests: a valid report to mutate, built once from the worked example.

Building from the committed example rather than from a literal in the test file is deliberate: if
the example ever stops being valid, these tests fail too, so the example a submitter is told to copy
cannot quietly rot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ifixai.register import store
from ifixai.register.spec import MisbehaviourReport

REPO_ROOT = Path(__file__).resolve().parents[3]
#: The worked examples, which double as the register fixture: a clone of the public register has the
#: same two directories at its own root (`ifixai.register.store`). They live inside the package
#: rather than in a top-level `examples/`, which this repository's .gitignore excludes — so they are
#: tracked, and they ship in the wheel for anyone who wants to copy one as a starting point.
REGISTER_ROOT = REPO_ROOT / "ifixai" / "register" / "examples"
ENTRY = REGISTER_ROOT / "entries" / "AGENT-2026-0001.yaml"
CANDIDATES = REGISTER_ROOT / "candidates"


@pytest.fixture
def entry() -> MisbehaviourReport:
    return store.load_report(ENTRY)


@pytest.fixture
def register_root() -> Path:
    return REGISTER_ROOT


@pytest.fixture
def candidate() -> MisbehaviourReport:
    files = sorted(CANDIDATES.glob("*.yaml"))
    assert files, "the register ships at least one worked candidate"
    return store.load_report(files[0])
