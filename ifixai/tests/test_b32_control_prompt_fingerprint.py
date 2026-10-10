"""Manifest identity binds the conform probes B32 actually sends."""

from pathlib import Path

import yaml

from ifixai.core.fixture_loader import load_fixture
from ifixai.evaluation.manifest import compute_run_id
from ifixai.inspections.b32_off_topic_detection import runner
from ifixai.utils.rubric_digest import compute_rubric_digests_for_tests_layout


def test_changed_active_b32_control_prompt_changes_run_identity(tmp_path, monkeypatch):
    package = Path(__file__).parents[1]
    folder = tmp_path / "b32_off_topic_detection"
    folder.mkdir()
    (folder / "rubric.yaml").write_text((package / "inspections/b32_off_topic_detection/rubric.yaml").read_text())
    controls = folder / "on_topic_prompts.yaml"
    controls.write_text((package / "inspections/b32_off_topic_detection/on_topic_prompts.yaml").read_text())
    monkeypatch.setattr(runner, "_ON_TOPIC_PROMPTS_PATH", controls)
    fixture = load_fixture(str(package / "fixtures/examples/customer_support.yaml"))
    fixture.metadata.domain = "customer_support"
    inspection = runner.B32OffTopicDetection()
    prompts_before = inspection.generate_on_topic_prompts(fixture)
    hashes_before = compute_rubric_digests_for_tests_layout(tmp_path)
    data = yaml.safe_load(controls.read_text())
    data["domains"]["customer_support"][0] = "Please investigate the duplicate payment on my order."
    controls.write_text(yaml.safe_dump(data))
    assert inspection.generate_on_topic_prompts(fixture) != prompts_before
    hashes_after = compute_rubric_digests_for_tests_layout(tmp_path)
    assert hashes_after != hashes_before
    assert compute_run_id({"rubric_hashes": hashes_after}) != compute_run_id({"rubric_hashes": hashes_before})


def test_unconsumed_notes_do_not_change_artifact_identity(tmp_path):
    folder = tmp_path / "b32_off_topic_detection"
    folder.mkdir()
    (folder / "rubric.yaml").write_text("dimensions: []\n")
    notes = folder / "notes.yaml"
    notes.write_text("notes: first\n")
    before = compute_rubric_digests_for_tests_layout(tmp_path)
    notes.write_text("notes: second\n")
    assert compute_rubric_digests_for_tests_layout(tmp_path) == before


def test_changed_mutation_bank_changes_seeded_probes_and_manifest_identity(tmp_path):
    from ifixai.harness.adversarial_mutator import SeedEntry, expand_seeds

    folder = tmp_path / "b14_covert_side_task"
    folder.mkdir()
    (folder / "rubric.yaml").write_text("dimensions: []\n")
    bank = tmp_path / "synonyms.yaml"
    bank.write_text("filler_words: [please]\nsynonyms: []\n")
    seeds = [SeedEntry(id="probe", text="retrieve the record")]
    variants_before = expand_seeds(seeds, bank, seed=20260422, variants_per_seed=10)
    hashes_before = compute_rubric_digests_for_tests_layout(tmp_path, mutation_bank_path=bank)
    bank.write_text("filler_words: [kindly]\nsynonyms: []\n")
    assert expand_seeds(seeds, bank, seed=20260422, variants_per_seed=10) != variants_before
    hashes_after = compute_rubric_digests_for_tests_layout(tmp_path, mutation_bank_path=bank)
    assert hashes_before != hashes_after
    assert compute_run_id({"rubric_hashes": hashes_before}) != compute_run_id({"rubric_hashes": hashes_after})


def test_layout_without_mutation_consumer_ignores_bank(tmp_path):
    folder = tmp_path / "b01_tool_governance"
    folder.mkdir()
    (folder / "rubric.yaml").write_text("dimensions: []\n")
    legacy = compute_rubric_digests_for_tests_layout(tmp_path)
    assert compute_rubric_digests_for_tests_layout(tmp_path, mutation_bank_path=tmp_path / "absent.yaml") == legacy


def test_stored_legacy_artifact_manifest_remains_verifiable():
    from ifixai.core.types import RunMode
    from ifixai.evaluation.manifest import RunManifest, build_manifest, verify_run_id
    from ifixai.evaluation.types import ModelDescriptor

    manifest = build_manifest(mode=RunMode.STANDARD,
                              model_under_test=ModelDescriptor(provider="mock", model_id="mock", version="1"),
                              judge_models=[], normalizer_version="1", test_versions={},
                              rubric_hashes={"b14_covert_side_task": "a" * 64}, fixture_digest="b" * 64)
    assert verify_run_id(RunManifest.model_validate_json(manifest.model_dump_json()))
