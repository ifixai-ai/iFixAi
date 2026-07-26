import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel

LOCK_FILENAME = "version_lock.json"


class TestInputs(BaseModel):
    __test__ = False

    test_id: str
    rubric_hash: str = ""
    fixture_hash: str = ""
    normalizer_version: str = ""
    inspection_corpus_version: str = ""
    judge_set_hash: str = ""

    def content_hash(self) -> str:
        canonical = "|".join([
            self.test_id,
            self.rubric_hash,
            self.fixture_hash,
            self.normalizer_version,
            self.inspection_corpus_version,
            self.judge_set_hash,
        ])
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class VersionLockEntry(BaseModel):
    declared_version: str
    input_hash: str


class VersionLockMismatch(RuntimeError):
    pass


def load_lock(lock_path: Path) -> dict[str, VersionLockEntry]:
    if not lock_path.exists():
        return {}
    raw = json.loads(lock_path.read_text())
    return {bid: VersionLockEntry.model_validate(entry) for bid, entry in raw.items()}


def save_lock(lock_path: Path, entries: dict[str, VersionLockEntry]) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = {bid: entry.model_dump() for bid, entry in entries.items()}
    lock_path.write_text(json.dumps(serialized, indent=2, sort_keys=True))


def verify_test_version(
    inputs: TestInputs,
    declared_version: str,
    lock: dict[str, VersionLockEntry],
) -> None:
    entry = lock.get(inputs.test_id)
    if entry is None:
        return
    current_hash = inputs.content_hash()
    if current_hash == entry.input_hash:
        return
    if declared_version == entry.declared_version:
        raise VersionLockMismatch(
            f"{inputs.test_id}: inputs changed but declared_version "
            f"{declared_version!r} was not bumped. Locked input_hash="
            f"{entry.input_hash}, current input_hash={current_hash}. "
            "Bump the test version and update version_lock.json."
        )


def verify_all(
    test_inputs: Iterable[tuple[TestInputs, str]],
    lock_path: Path,
) -> None:
    lock = load_lock(lock_path)
    errors: list[str] = []
    for inputs, declared_version in test_inputs:
        try:
            verify_test_version(inputs, declared_version, lock)
        except VersionLockMismatch as exc:
            errors.append(str(exc))
    if errors:
        raise VersionLockMismatch("\n".join(errors))
