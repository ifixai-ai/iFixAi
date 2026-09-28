import json
import warnings

from ifixai.core.types import RunMode
from ifixai.evaluation.manifest import (
    build_manifest,
    compute_run_id,
    load_manifest,
    verify_run_id,
)
from ifixai.evaluation.types import ModelDescriptor


def _manifest_payload(schema_version: int) -> dict:
    current = build_manifest(
        mode=RunMode.STANDARD,
        model_under_test=ModelDescriptor(provider="mock", model_id="sut", version="1"),
        judge_models=[],
        normalizer_version="1",
        test_versions={},
        rubric_hashes={},
        fixture_digest="a" * 64,
        run_nonce="0123456789abcdef",
        timestamp="2026-01-01T00:00:00Z",
    )
    payload = current.model_dump(mode="json")
    payload["schema_version"] = schema_version
    if schema_version < 3:
        for field in ("b29_seed", "b32_seed", "b29_seed_pinned", "b32_seed_pinned"):
            del payload[field]
    if schema_version == 1:
        for field in (
            "schema_version", "run_nonce", "holdout_seed", "holdout_ids",
            "b12_seed_pinned", "b14_seed_pinned", "b28_seed_pinned", "b30_seed_pinned",
        ):
            del payload[field]
    payload["run_id"] = compute_run_id(payload)
    return payload


def test_verifies_historical_manifest_schemas(tmp_path):
    for version in (1, 2, 3):
        path = tmp_path / f"manifest-v{version}.json"
        path.write_text(json.dumps(_manifest_payload(version)), encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            manifest = load_manifest(path)
        assert verify_run_id(manifest), f"valid schema v{version} manifest was rejected"

        tampered = manifest.model_copy(update={"normalizer_version": "changed"})
        assert not verify_run_id(tampered), f"tampered schema v{version} manifest passed"


def test_v2_load_warning_does_not_claim_missing_replay_protection(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest_payload(2)), encoding="utf-8")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manifest = load_manifest(path)

    assert verify_run_id(manifest)
    assert all("obtain replay protection" not in str(w.message) for w in caught)
