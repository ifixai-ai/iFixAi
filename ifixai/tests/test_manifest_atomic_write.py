import errno
import json
import stat
from pathlib import Path

import pytest

from ifixai.core.types import RunMode, TestResult, TestStatus
from ifixai.evaluation.checkpoint import load_checkpoint, save_checkpoint
from ifixai.evaluation.manifest import (
    build_manifest,
    load_manifest,
    verify_run_id,
    write_manifest,
)
from ifixai.evaluation.types import ModelDescriptor


def manifest(timestamp):
    return build_manifest(
        mode=RunMode.STANDARD,
        model_under_test=ModelDescriptor(provider="mock", model_id="owned", version="1"),
        judge_models=[], normalizer_version="1", test_versions={}, rubric_hashes={},
        fixture_digest="a" * 64, run_nonce="b" * 16, timestamp=timestamp,
    )


@pytest.mark.parametrize("failure", [OSError(errno.ENOSPC, "owned disk-full injection"),
                                    KeyboardInterrupt()])
def test_failed_resume_manifest_write_preserves_existing_run(tmp_path, monkeypatch, failure):
    original = manifest("2026-10-10T12:00:00Z")
    path = write_manifest(original, tmp_path)
    path.chmod(0o640)
    before = path.read_bytes()
    completed = TestResult(test_id="B01", score=1, passed=True, passing=True,
                           status=TestStatus.PASS)
    save_checkpoint(tmp_path, original.run_id, {"B01": completed})
    replacement = manifest("2026-10-10T13:00:00Z")
    assert replacement.run_id == original.run_id

    def interrupted_write(target, data, *args, **kwargs):
        # Exercise a genuine truncating write before the injected interruption.
        with target.open("w", encoding="utf-8") as handle:
            handle.write(data[:10])
            handle.flush()
        raise failure

    monkeypatch.setattr(Path, "write_text", interrupted_write)
    with pytest.raises(type(failure)):
        write_manifest(replacement, tmp_path)
    assert path.read_bytes() == before
    recovered = load_manifest(path)
    assert verify_run_id(recovered)
    assert load_checkpoint(tmp_path, recovered.run_id) == {"B01": completed}
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert sorted(p.name for p in path.parent.iterdir()) == ["checkpoint.json", "manifest.json"]


def test_successful_resume_manifest_replacement_keeps_identity_and_mode(tmp_path):
    original = manifest("2026-10-10T12:00:00Z")
    path = write_manifest(original, tmp_path)
    path.chmod(0o640)
    replacement = manifest("2026-10-10T13:00:00Z")
    assert write_manifest(replacement, tmp_path) == path
    recovered = load_manifest(path)
    assert recovered == replacement
    assert verify_run_id(recovered)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert [p.name for p in path.parent.iterdir()] == ["manifest.json"]
    assert json.loads(path.read_text(encoding="utf-8"))["timestamp"] == replacement.timestamp
