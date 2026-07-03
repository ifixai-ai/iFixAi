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
# b01..b32 (Categories I–V) and p01..p32 (Categories VI–XII — SABOTAGE / SUBVERSION /
# CONCEALMENT / SANDBAGGING / INSUBORDINATION / USURPATION / SYSTEMIC_RISK; p19_* is
# SANDBAGGING, Category IX). c01..c16 (C-series — Capability-Reliability, Categories
# XIV–XXI); c02_* is the first C-series folder, so the 'c' prefix with a 01..16 NN range
# is added here. s01..s08 (S-series — Stakeholder & Multi-Principal Integrity, Category
# XVIII); s02_* is the first S-series folder, so the 's' prefix with a 01..08 NN range is
# added here.
# x01..x11 (X-series — Gap-closure, Categories XXII–XXVI); x04_* is the first X-series
# folder, so the 'x' prefix with a 01..11 NN range is added here.
_FOLDER_NAME_PATTERN = re.compile(
    r"^([bp](0[1-9]|[12][0-9]|3[0-2])|c(0[1-9]|1[0-6])|s(0[1-8])"
    r"|x(0[1-9]|1[0-1]))_[a-z0-9_]+$"
)
# P19/P27/P32/S02 ship domain-neutral corpora (honeypot restraint / privilege
# creep / systemic-harm / stakeholder-conflict); their folders are already
# admitted by _FOLDER_NAME_PATTERN above.
_CORPUS_TEST_IDS: frozenset[str] = frozenset(
    {"B12", "B14", "B28", "B30", "P13", "P19", "P22", "P27", "P32", "S02"}
)
# Structural-only tests score via % correct decisions, not an LLM rubric judge;
# they must not ship rubric.yaml / references.yaml (the files would imply
# dimensions that are never evaluated). Scoring mechanism per test:
#   B23 — four runner sub-probes (audit_rule_linkage, version_non_trivial,
#         bundle_digest_present, version_reproducible)
#   C02 — get_confidence (% abstention on the below-threshold subset)
#   C05 — route_to_human (% below-threshold cases routed to human/manual)
#   C11 — reconcile_outcome (% adverse-drift feeds correctly reconciled)
#   X04 — evaluate_deployment_gate (% breach windows correctly blocked/flagged)
#   X11 — evaluate_confirmation_gate (% breach requests correctly blocked/escalated)
_STRUCTURAL_ONLY_TEST_IDS: frozenset[str] = frozenset(
    {"B01", "B02", "B04", "B23", "P01", "P08", "C02", "C05", "C11", "X04", "X11"}
)
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
    # Derive the id prefix from the leading folder letter ('b' or 'p') rather
    # than hardcoding 'B', so a P-series folder (p01_*) resolves to 'P01', not
    # 'B01'. The folder name has already matched _FOLDER_NAME_PATTERN.
    prefix = folder.name[0].upper()
    folder_nn = folder.name[1:3]
    test_id_for_check = f"{prefix}{folder_nn}"
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

    expected_id = f"{prefix}{folder_nn}"
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
