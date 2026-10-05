import pytest

from ifixai.evaluation.manifest import compute_run_id
from ifixai.utils.rubric_digest import compute_rubric_digests_for_tests_layout


@pytest.mark.parametrize(
    "artifact",
    [
        "corpus.yaml",
        "definition.yaml",
        "references.yaml",
        "rubric_comply.yaml",
        "rubric_refuse.yaml",
    ],
)
def test_actual_inspection_artifact_change_invalidates_manifest_identity(
    tmp_path, artifact
):
    folder = tmp_path / "b12_prompt_injection"
    folder.mkdir()
    (folder / "rubric.yaml").write_text("dimensions: []\n")
    changed = folder / artifact
    changed.write_text("version: before\n")
    before = compute_rubric_digests_for_tests_layout(tmp_path)
    changed.write_text("version: after\n")
    after = compute_rubric_digests_for_tests_layout(tmp_path)
    assert before != after
    assert compute_run_id({"rubric_hashes": before}) != compute_run_id(
        {"rubric_hashes": after}
    )


def test_legacy_default_rubric_and_prompts_keys_are_retained(tmp_path):
    folder = tmp_path / "b12_prompt_injection"
    folder.mkdir()
    (folder / "rubric.yaml").write_text("dimensions: []\n")
    (folder / "prompts.yaml").write_text("prompts: []\n")
    assert set(compute_rubric_digests_for_tests_layout(tmp_path)) == {
        "b12_prompt_injection",
        "b12_prompt_injection:prompts",
    }
