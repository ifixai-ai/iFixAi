"""Each checkpoint save owns its staging file, including overlapping saves."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from ifixai.core.types import TestResult
from ifixai.evaluation.checkpoint import load_checkpoint, save_checkpoint


def test_overlapping_saves_publish_complete_checkpoints(tmp_path, monkeypatch):
    barrier = Barrier(2)
    replace = Path.replace

    def synchronized_replace(self, target):
        if Path(target).name == "checkpoint.json":
            barrier.wait(timeout=5)
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", synchronized_replace)
    results = [{key: TestResult(test_id=key)} for key in ("B01", "B08")]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save_checkpoint, tmp_path, "run-1", r) for r in results]
        for future in futures:
            assert future.result() == tmp_path / "run-1" / "checkpoint.json"

    assert set(load_checkpoint(tmp_path, "run-1")) in ({"B01"}, {"B08"})
    assert list((tmp_path / "run-1").iterdir()) == [tmp_path / "run-1" / "checkpoint.json"]


def test_failed_publication_preserves_previous_checkpoint(tmp_path, monkeypatch):
    checkpoint = save_checkpoint(tmp_path, "run-1", {"B01": TestResult(test_id="B01")})
    previous = checkpoint.read_bytes()

    def reject_replace(self, target):
        raise OSError("controlled publication failure")

    monkeypatch.setattr(Path, "replace", reject_replace)
    with pytest.raises(OSError, match="controlled publication failure"):
        save_checkpoint(tmp_path, "run-1", {"B08": TestResult(test_id="B08")})

    assert checkpoint.read_bytes() == previous
    assert list(checkpoint.parent.iterdir()) == [checkpoint]
