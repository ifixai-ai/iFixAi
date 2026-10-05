"""Active non-B artifacts must invalidate a resumed run after edits."""

import shutil
from pathlib import Path

import pytest
import yaml

from ifixai.evaluation.manifest import compute_run_id
from ifixai.utils.rubric_digest import compute_rubric_digests_for_tests_layout

INSPECTIONS = Path(__file__).parents[1] / "inspections"


@pytest.mark.parametrize(
    "folder_name,artifact",
    [
        ("p13_hidden_side_objective_persistence", "rubric.yaml"),
        ("p13_hidden_side_objective_persistence", "corpus.yaml"),
        ("s02_configurer_stakeholder_conflict", "rubric.yaml"),
        ("s02_configurer_stakeholder_conflict", "corpus.yaml"),
        ("v01_overcorrection_check", "rubric.yaml"),
        ("v01_overcorrection_check", "definition.yaml"),
    ],
)
def test_non_b_inspection_edits_change_resume_identity(tmp_path, folder_name, artifact):
    # Retain a B inspection so the old implementation returns an apparently
    # valid fingerprint instead of merely raising for a missing B rubric.
    shutil.copytree(INSPECTIONS / "b12_prompt_injection", tmp_path / "b12_prompt_injection")
    copied = tmp_path / folder_name
    shutil.copytree(INSPECTIONS / folder_name, copied)
    before = compute_rubric_digests_for_tests_layout(tmp_path)
    source = copied / artifact
    original = source.read_text(encoding="utf-8")
    # Modify an existing string in the actual shipped artifact, rather than
    # introducing a schema field that the inspection would ignore.
    parsed = yaml.safe_load(original)

    def revise_first_text(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and len(child) > 20:
                    value[key] = child + " revised"
                    return True
                if revise_first_text(child):
                    return True
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, str) and len(child) > 20:
                    value[index] = child + " revised"
                    return True
                if revise_first_text(child):
                    return True
        return False

    assert revise_first_text(parsed)
    source.write_text(yaml.safe_dump(parsed), encoding="utf-8")
    after = compute_rubric_digests_for_tests_layout(tmp_path)
    assert before != after
    assert compute_run_id({"rubric_hashes": before}) != compute_run_id({"rubric_hashes": after})


def test_fingerprint_covers_all_shipped_analytic_rubrics():
    hashes = compute_rubric_digests_for_tests_layout(INSPECTIONS)
    expected = {path.parent.name for path in INSPECTIONS.glob("*/rubric.yaml")}
    assert expected <= hashes.keys()
