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
ENTRY = REPO_ROOT / "register" / "entries" / "IFX-2026-0101.yaml"
CANDIDATES = REPO_ROOT / "register" / "candidates"


@pytest.fixture
def entry() -> MisbehaviourReport:
    return store.load_report(ENTRY)


@pytest.fixture
def candidate() -> MisbehaviourReport:
    files = sorted(CANDIDATES.glob("*.yaml"))
    assert files, "the register ships at least one worked candidate"
    return store.load_report(files[0])
