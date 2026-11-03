"""An old verifier cannot attest to a future measurement contract."""

import json

import pytest
from pydantic import ValidationError

from ifixai.core.types import RunMode
from ifixai.evaluation.manifest import (
    RunManifest,
    compute_run_id,
    load_manifest,
    verify_run_id,
)
from ifixai.evaluation.types import ModelDescriptor


def manifest():
    return RunManifest(run_id="", timestamp="now", mode=RunMode.STANDARD,
                       model_under_test=ModelDescriptor(provider="mock", model_id="mock", version="1"),
                       normalizer_version="1", test_versions={}, fixture_digest="a" * 64)


@pytest.mark.parametrize("version", [5, 100])
def test_future_manifest_schema_is_rejected_at_ingestion(tmp_path, version):
    raw = manifest().model_dump(mode="json")
    raw["schema_version"] = version
    raw["run_id"] = compute_run_id(raw)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValidationError, match="schema_version"):
        load_manifest(path)


def test_verification_fails_closed_for_bypassed_future_schema():
    future = manifest().model_copy(update={"schema_version": 5})
    future.run_id = compute_run_id(future.model_dump(mode="json"))
    assert not verify_run_id(future)


def test_current_manifest_verification_remains_supported():
    current = manifest()
    current.run_id = compute_run_id(current.model_dump(mode="json"))
    assert verify_run_id(current)
