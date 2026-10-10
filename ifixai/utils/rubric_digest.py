import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

_YAML_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml"})


class MissingRubricError(FileNotFoundError):
    """Raised when an expected rubric YAML or rubrics directory is unreadable
    at manifest-construction time. Subclasses FileNotFoundError so callers
    that already handle filesystem errors continue to work; the dedicated
    class lets test code assert on this specific condition without catching
    unrelated filesystem failures."""


def _canonicalise(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _canonicalise(obj[key]) for key in sorted(obj)}
    if isinstance(obj, list):
        return [_canonicalise(item) for item in obj]
    return obj


def compute_rubric_digest(rubric_path: Path | str) -> str:
    path = Path(rubric_path)
    if not path.is_file():
        raise MissingRubricError(f"rubric YAML not found or not a regular file: {path}")
    raw = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    canonical = _canonicalise(parsed)
    serialised = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def compute_rubric_digests_for_directory(rubrics_dir: Path | str) -> dict[str, str]:
    directory = Path(rubrics_dir)
    if not directory.is_dir():
        raise MissingRubricError(
            f"rubrics directory not found or not a directory: {directory}"
        )
    yaml_files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix in _YAML_SUFFIXES
    )
    if not yaml_files:
        raise MissingRubricError(
            f"rubrics directory contains no YAML files: {directory}"
        )
    return {path.name: compute_rubric_digest(path) for path in yaml_files}


def compute_rubric_digests_for_tests_layout(
    tests_dir: Path | str, *, mutation_bank_path: Path | str | None = None
) -> dict[str, str]:
    directory = Path(tests_dir)
    if not directory.is_dir():
        raise MissingRubricError(
            f"tests directory not found or not a directory: {directory}"
        )
    rubric_files = sorted(directory.glob("*/rubric.yaml"))
    if not rubric_files:
        raise MissingRubricError(
            f"tests directory contains no per-test rubric.yaml files: {directory}"
        )
    hashes: dict[str, str] = {
        path.parent.name: compute_rubric_digest(path) for path in rubric_files
    }
    # A pinned seed is reproducible only with the exact prompt corpus, plan,
    # references and outcome-specific rubric that the inspection consumes.
    # Preserve the existing default-rubric key and legacy prompts key.
    for folder in sorted({path.parent for path in rubric_files}):
        artifacts = sorted(
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix in _YAML_SUFFIXES
            and path.stem != "rubric"
            and (
                path.stem in {"prompts", "on_topic_prompts", "corpus", "definition", "references"}
                or path.stem.startswith("rubric_")
            )
        )
        for artifact in artifacts:
            key = f"{folder.name}:{artifact.stem}"
            hashes[key] = compute_rubric_digest(artifact)
    mutation_consumers = {
        "b14_covert_side_task", "b28_rag_context_integrity", "b30_malicious_deployer_rules"
    }
    if mutation_bank_path is not None and any(
        path.parent.name in mutation_consumers for path in rubric_files
    ):
        hashes["harness:synonyms"] = compute_rubric_digest(mutation_bank_path)
    return hashes
