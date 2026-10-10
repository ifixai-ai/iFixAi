from ifixai.core.types import RunMode
from ifixai.evaluation.manifest import RunManifest, compute_run_id, verify_run_id
from ifixai.evaluation.types import ModelDescriptor


def test_schema_v2_verifies_its_original_fields():
    manifest = RunManifest(
        run_id="",
        timestamp="now",
        schema_version=2,
        run_nonce="a" * 16,
        mode=RunMode.STANDARD,
        model_under_test=ModelDescriptor(
            provider="mock", model_id="model", version="1"
        ),
        normalizer_version="1",
        test_versions={},
        fixture_digest="a" * 64,
        holdout_seed=42,
        holdout_ids={"actor": "held-out"},
    )
    fields = manifest.model_dump(
        mode="json",
        exclude={
            "run_id",
            "timestamp",
            "sut_context_digest",
            "b29_seed",
            "b32_seed",
            "b29_seed_pinned",
            "b32_seed_pinned",
        },
    )
    manifest.run_id = compute_run_id(fields)
    assert verify_run_id(manifest)
    assert not verify_run_id(manifest.model_copy(update={"holdout_seed": 43}))
