import errno
from pathlib import Path

import pytest

from ifixai.cli.reports import save_reports
from ifixai.core.types import TestRunResult


@pytest.mark.parametrize(
    "report_format,suffix", [("json", ".json"), ("markdown", "-summary.md")]
)
def test_failed_report_write_preserves_the_previous_artifact(
    tmp_path, monkeypatch, report_format, suffix
):
    result = TestRunResult(system_name="agent", fixture_name="support")
    target = tmp_path / f"ifixai-agent-support{suffix}"
    old = b"previous successful report\n"
    target.write_bytes(old)
    original = Path.write_text
    writes = []

    def fail_during_actual_write(path, data, *args, **kwargs):
        writes.append(path)
        with path.open("w", encoding="utf-8") as handle:
            handle.write(data[:10])
            handle.flush()
        raise OSError(errno.ENOSPC, "owned injected disk-full failure")

    monkeypatch.setattr(Path, "write_text", fail_during_actual_write)
    with pytest.raises(OSError) as exc:
        save_reports(result, str(tmp_path), report_format)
    assert exc.value.errno == errno.ENOSPC
    assert writes
    assert target.read_bytes() == old
    assert list(tmp_path.iterdir()) == [target]
    monkeypatch.setattr(Path, "write_text", original)


def test_successful_json_report_still_publishes_normally(tmp_path):
    import json

    save_reports(
        TestRunResult(system_name="agent", fixture_name="support"),
        str(tmp_path),
        "json",
    )
    assert (
        json.loads((tmp_path / "ifixai-agent-support.json").read_text())["metadata"][
            "system_name"
        ]
        == "agent"
    )


def test_successful_replacement_preserves_existing_report_permissions(tmp_path):
    import os
    import stat

    if os.name == "nt":
        pytest.skip("POSIX permission bits")
    target = tmp_path / "ifixai-agent-support.json"
    target.write_text("old")
    target.chmod(0o640)
    save_reports(
        TestRunResult(system_name="agent", fixture_name="support"),
        str(tmp_path),
        "json",
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert len(list(tmp_path.iterdir())) == 1
