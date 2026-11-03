"""Evidence creation must not clobber another writer or follow a symlink."""
from pathlib import Path

import pytest

from ifixai.windtunnel.defects import CLEAN
from ifixai.windtunnel.evidence import create_bundle, write_bundle
from ifixai.windtunnel.measure import WindTunnelReport


def _bundle():
    return create_bundle(((CLEAN, WindTunnelReport("clean", ())),), "customer_support")


def test_dangling_symlink_is_never_followed(tmp_path):
    target = tmp_path / "unrelated"
    destination = tmp_path / "evidence.json"
    destination.symlink_to(target)
    with pytest.raises(ValueError, match="never overwritten"):
        write_bundle(_bundle(), destination)
    assert not target.exists()


def test_intervening_writer_is_not_overwritten(tmp_path, monkeypatch):
    destination = tmp_path / "evidence.json"
    original = Path.mkdir
    def raced_mkdir(path, *args, **kwargs):
        original(path, *args, **kwargs)
        if path == destination.parent:
            destination.write_text("other writer's evidence")
    monkeypatch.setattr(Path, "mkdir", raced_mkdir)
    with pytest.raises(ValueError, match="never overwritten"):
        write_bundle(_bundle(), destination)
    assert destination.read_text() == "other writer's evidence"
