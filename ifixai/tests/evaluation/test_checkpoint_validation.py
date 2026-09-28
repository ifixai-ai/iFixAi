"""Damaged checkpoint data must never silently skip the wrong inspection."""

import json

from ifixai.core.types import TestResult, TestStatus
from ifixai.evaluation.checkpoint import load_checkpoint


def _write_checkpoint(tmp_path, payload):
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "checkpoint.json").write_text(json.dumps(payload), encoding="utf-8")


def test_non_mapping_checkpoint_is_ignored(tmp_path):
    _write_checkpoint(tmp_path, [{"test_id": "B01"}])

    assert load_checkpoint(tmp_path, "run-1") == {}


def test_checkpoint_entry_with_mismatched_id_is_rerun(tmp_path):
    _write_checkpoint(tmp_path, {
        "B01": TestResult(test_id="B08", status=TestStatus.PASS).model_dump(mode="json")
    })

    assert load_checkpoint(tmp_path, "run-1") == {}


def test_error_status_is_rerun_even_without_error_message(tmp_path):
    _write_checkpoint(tmp_path, {
        "B01": TestResult(test_id="B01", status=TestStatus.ERROR).model_dump(mode="json")
    })

    assert load_checkpoint(tmp_path, "run-1") == {}


def test_valid_completed_entry_is_reused(tmp_path):
    _write_checkpoint(tmp_path, {
        "B01": TestResult(test_id="B01", status=TestStatus.PASS).model_dump(mode="json")
    })

    assert load_checkpoint(tmp_path, "run-1")["B01"].status == TestStatus.PASS
