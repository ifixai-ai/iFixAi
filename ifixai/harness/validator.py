import re
from pathlib import Path

import yaml

_TESTS_DIR = Path(__file__).parent.parent / "inspections"
_REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "definition.yaml",
    "rubric.yaml",
    "references.yaml",
    "runner.py",
)
_FOLDER_NAME_PATTERN = re.compile(r"^b(0[1-9]|[12][0-9]|3[0-2])_[a-z0-9_]+$")
_CORPUS_TEST_IDS: frozenset[str] = frozenset({"B12", "B14", "B28", "B30"})
# Structural-only tests score via % correct decisions, not via LLM rubric judge.
# They must not have rubric.yaml / references.yaml — the files would imply
# dimensions that are never actually evaluated.
_STRUCTURAL_ONLY_TEST_IDS: frozenset[str] = frozenset({"B01", "B02", "B04"})
# Tests that score via an LLM judge (atomic-claims path) but do NOT use the
# analytic-rubric pipeline. rubric.yaml would advertise dimensions that are
# never evaluated, so these tests may omit it.
_ATOMIC_JUDGE_ONLY_TEST_IDS: frozenset[str] = frozenset()


class LayoutValidationError(Exception):
    pass


def _iter_test_folders(tests_dir: Path) -> list[Path]:
    if not tests_dir.is_dir():
        raise LayoutValidationError(f"inspections directory missing: {tests_dir}")
    return sorted(
        p
        for p in tests_dir.iterdir()
        if p.is_dir() and _FOLDER_NAME_PATTERN.match(p.name)
    )


def _validate_folder(folder: Path) -> str:
    folder_nn = folder.name[1:3]
    test_id_for_check = f"B{folder_nn}"
    is_structural_only = test_id_for_check in _STRUCTURAL_ONLY_TEST_IDS
    is_atomic_judge_only = test_id_for_check in _ATOMIC_JUDGE_ONLY_TEST_IDS
    rubric_artifacts = {"rubric.yaml", "references.yaml"}

    for artifact in _REQUIRED_ARTIFACTS:
        if is_structural_only and artifact in rubric_artifacts:
            path = folder / artifact
            if path.is_file():
                raise LayoutValidationError(
                    f"test folder {folder.name!r} is structural-only but contains "
                    f"{artifact!r} — delete it to prevent advertised-but-unmeasured dimensions"
                )
            continue
        if is_atomic_judge_only and artifact in rubric_artifacts:
            # Atomic-judge-only tests score via evaluate_atomic, not the analytic
            # rubric pipeline. rubric.yaml is optional — skip the existence check.
            continue
        path = folder / artifact
        if not path.is_file():
            raise LayoutValidationError(
                f"test folder {folder.name!r} is missing required artifact {artifact!r}"
            )

    definition_path = folder / "definition.yaml"
    try:
        raw = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LayoutValidationError(
            f"definition.yaml in {folder.name!r} is not valid YAML: {exc}"
        ) from exc
    if not isinstance(raw, dict) or "test_id" not in raw:
        raise LayoutValidationError(
            f"definition.yaml in {folder.name!r} missing required key 'test_id'"
        )
    test_id = raw["test_id"]

    expected_id = f"B{folder_nn}"
    if test_id != expected_id:
        raise LayoutValidationError(
            f"test folder {folder.name!r} declares test_id={test_id!r} "
            f"but folder name implies {expected_id!r}"
        )

    corpus_path = folder / "corpus.yaml"
    has_corpus_file = corpus_path.is_file()
    expects_corpus = test_id in _CORPUS_TEST_IDS
    if expects_corpus and not has_corpus_file:
        raise LayoutValidationError(
            f"test {test_id!r} requires corpus.yaml but it is missing in {folder.name!r}"
        )
    if has_corpus_file and not expects_corpus:
        raise LayoutValidationError(
            f"test {test_id!r} unexpectedly has corpus.yaml in {folder.name!r}"
        )

    return test_id


def validate_layout(tests_dir: Path | None = None) -> list[str]:
    root = tests_dir or _TESTS_DIR
    seen_ids: dict[str, str] = {}
    validated: list[str] = []
    for folder in _iter_test_folders(root):
        test_id = _validate_folder(folder)
        if test_id in seen_ids:
            raise LayoutValidationError(
                f"duplicate test_id {test_id!r}: folders {seen_ids[test_id]!r} and {folder.name!r}"
            )
        seen_ids[test_id] = folder.name
        validated.append(test_id)
    return validated
