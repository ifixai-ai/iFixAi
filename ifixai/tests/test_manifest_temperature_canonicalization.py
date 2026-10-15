"""Manifest identities use the same temperature representation as their models."""

import pytest

from ifixai.core.types import RunMode
from ifixai.evaluation.manifest import (
    build_manifest,
    load_manifest,
    verify_run_id,
    write_manifest,
)
from ifixai.evaluation.types import ModelDescriptor

TEMPERATURE_FIELDS = (
    "judge_temperature",
    "sut_temperature",
    "effective_sut_temperature",
)


def manifest(**changes):
    return build_manifest(
        mode=RunMode.STANDARD,
        model_under_test=ModelDescriptor(provider="mock", model_id="mock", version="1"),
        judge_models=[],
        normalizer_version="1",
        test_versions={},
        rubric_hashes={},
        fixture_digest="a" * 64,
        run_nonce="0123456789abcdef",
        timestamp="2026-10-15T00:00:00Z",
        **changes,
    )


@pytest.mark.parametrize("field", TEMPERATURE_FIELDS)
@pytest.mark.parametrize("value", [0, 1, 2])
def test_integer_temperature_verifies_and_matches_float_after_roundtrip(tmp_path, field, value):
    integer = manifest(**{field: value})
    floating = manifest(**{field: float(value)})

    assert type(getattr(integer, field)) is float
    assert verify_run_id(integer)
    assert integer.run_id == floating.run_id
    assert verify_run_id(floating)

    path = write_manifest(integer, tmp_path)
    loaded = load_manifest(path)
    assert loaded.run_id == integer.run_id
    assert verify_run_id(loaded)


@pytest.mark.parametrize("field", TEMPERATURE_FIELDS)
def test_fractional_temperature_still_verifies(field):
    assert verify_run_id(manifest(**{field: 0.5}))


@pytest.mark.parametrize("field", TEMPERATURE_FIELDS)
def test_actual_temperature_change_still_changes_identity_and_rejects_tampering(field):
    original = manifest(**{field: 0.0})
    changed = manifest(**{field: 1.0})
    assert original.run_id != changed.run_id
    assert not verify_run_id(original.model_copy(update={field: 1.0}))


def test_existing_float_manifest_identity_is_unchanged():
    # Frozen from the original schema-v4 builder with all default temperatures.
    assert manifest().run_id == "3df8142e12aac9d7"
