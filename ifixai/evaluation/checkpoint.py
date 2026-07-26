"""Run-level checkpoint + resume.

A run's state (which tests have finished, with what results) is written
to disk after each test completes. Re-invoking the CLI with the same
config + seed + corpus versions loads the checkpoint and executes only
the remaining tests.

Checkpoint safety:
- Keyed by `run_id` from the manifest, a SHA-256 over every field that
  would invalidate reproducibility (capabilities, seed, corpus versions,
  scoring_logic_version, model, judges, fixture). Two runs with
  incompatible configs get different `run_id`s, so a stale checkpoint
  cannot pollute a fresh run.
- Resume re-executes any cached result that has an `error_message`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from ifixai.core.types import TestResult
from ifixai.evaluation.manifest import RunManifest
from ifixai.evaluation.schemas import ResumeState

_logger = logging.getLogger(__name__)


def _checkpoint_path(runs_root: Path, run_id: str) -> Path:
    return runs_root / run_id / "checkpoint.json"


def load_checkpoint(runs_root: Path, run_id: str) -> dict[str, TestResult]:
    path = _checkpoint_path(runs_root, run_id)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("checkpoint unreadable (%s) — starting fresh", exc)
        return {}
    out: dict[str, TestResult] = {}
    for test_id, payload in raw.items():
        try:
            result = TestResult.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 — best-effort checkpoint read; any failure is treated as no checkpoint
            _logger.warning(
                "checkpoint entry for %s rejected (%s) — will re-run",
                test_id, exc,
            )
            continue
        if result.error_message:
            _logger.info("skipping stale errored result for %s", test_id)
            continue
        out[test_id] = result
    return out


def save_checkpoint(
    runs_root: Path,
    run_id: str,
    results: dict[str, TestResult],
) -> Path:
    """Persist atomically: writes to a sibling `.tmp` file then renames,
    so the checkpoint is never half-written if the process dies mid-write."""
    path = _checkpoint_path(runs_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = {
        test_id: result.model_dump(mode="json")
        for test_id, result in results.items()
    }
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def clear_checkpoint(runs_root: Path, run_id: str) -> bool:
    path = _checkpoint_path(runs_root, run_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def checkpoint_path(runs_root: Path, run_id: str) -> Path:
    return _checkpoint_path(runs_root, run_id)


def describe_resume(
    runs_root: Path,
    manifest: RunManifest,
    all_test_ids: list[str],
) -> ResumeState:
    cached = load_checkpoint(runs_root, manifest.run_id)
    remaining = [b for b in all_test_ids if b not in cached]
    return ResumeState(cached=cached, remaining=remaining)


class BlockedInspectionError(RuntimeError):
    """Raised by preflight when `--eval-mode self` hits a inspection that
    forbids self-judging.

    The run is aborted before any test executes. The message tells the
    user to switch to an external judge via `--eval-mode semantic|full`
    or to target only the allowed inspections via `--test <id>`."""

    def __init__(self, blocked: list[str]) -> None:
        self.blocked = sorted(blocked)
        super().__init__(
            "The following inspections forbid self-judging because they "
            "measure behaviors the model itself is suspected of exhibiting:\n  "
            + ", ".join(self.blocked)
            + "\n\nTo proceed:\n"
              "  Switch to an external judge, e.g.:\n"
              "    --eval-mode semantic --judge-provider <provider> "
              "--judge-model <model>\n"
              "  Or run only the allowed inspections via repeated --test <id>.\n"
              "\nAny tests already completed in a previous run with the "
              "same config are preserved and will be skipped on resume."
        )


def preflight_eval_mode(
    eval_mode: str,
    inspection_specs: list,
    skip: Optional[list[str]] = None,
) -> None:
    """Raise `BlockedInspectionError` if `--eval-mode self` hits a forbidden
    inspection. Called before any test runs so the user fails fast."""
    if eval_mode != "self":
        return
    skip_set = set(skip or [])
    blocked = [
        spec.test_id
        for spec in inspection_specs
        if not spec.self_judge_allowed and spec.test_id not in skip_set
    ]
    if blocked:
        raise BlockedInspectionError(blocked)
