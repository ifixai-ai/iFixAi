"""`.ifixai/regressions.yaml`: findings a repository has promised not to repeat.

Pinning a finding adds an entry here. Every later `ifixai audit` runs the
pinned inspections whatever suite was asked for, and fails when one of them
fails again. Hosted iFixAi audits pin their findings in the same file, with a
`hosted:` reference instead of an inspection id. The local run counts those
and leaves them alone: a `hosted:` entry is a record of what was pinned, not a
local check.

Format (version 1):

    version: 1
    regressions:
      - inspection: B08
        case: B08_step1_user        # optional: the evidence item that failed
        title: Privilege Escalation Detection
        added: 2026-11-11
        note: Granted admin-only tool access to a user who asked urgently.
      - hosted:
          run_id: run_123
          finding: f-2
        title: Gave a refund above its limit
        added: 2026-11-11
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from ifixai.selfcheck.result import FAIL, find_test, finding_from_test

DEFAULT_PATH = Path(".ifixai") / "regressions.yaml"
VERSION = 1
INSPECTION_ID = re.compile(r"^[A-Z]{1,3}[0-9]{1,3}[A-Z]?$")
MAX_BYTES = 1024 * 1024

HEADER = (
    "# Findings this repository has promised not to repeat.\n"
    "# `ifixai audit` runs every `inspection:` entry and fails if one fails again;\n"
    "# `hosted:` entries record findings pinned from hosted iFixAi audits.\n"
    "# Add one with `ifixai findings add-regression <report> <inspection>`.\n"
)


class RegressionFileError(ValueError):
    """The regressions file is not one this version can read."""


def load(path: str | Path = DEFAULT_PATH) -> list[dict[str, Any]]:
    """The entries in the file; an absent file is an empty list."""
    source = Path(path)
    if not source.exists():
        return []
    raw = source.read_bytes()
    if len(raw) > MAX_BYTES:
        raise RegressionFileError(f"{source} is larger than 1 MiB")
    try:
        document = yaml.safe_load(raw.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RegressionFileError(f"{source} is not valid YAML: {exc}") from None
    if not isinstance(document, dict):
        raise RegressionFileError(f"{source} must be a mapping with `version` and `regressions`")
    if document.get("version", VERSION) != VERSION:
        raise RegressionFileError(
            f"{source} is version {document.get('version')!r}; this ifixai reads version {VERSION}"
        )
    entries = document.get("regressions") or []
    if not isinstance(entries, list):
        raise RegressionFileError(f"{source}: `regressions` must be a list")
    for index, entry in enumerate(entries):
        validate_entry(entry, f"{source}: regressions[{index}]")
    return entries


def validate_entry(entry: Any, where: str) -> None:
    if not isinstance(entry, dict):
        raise RegressionFileError(f"{where} must be a mapping")
    has_local = "inspection" in entry
    has_hosted = "hosted" in entry
    if has_local == has_hosted:
        raise RegressionFileError(f"{where} needs exactly one of `inspection` or `hosted`")
    if has_local:
        inspection = entry["inspection"]
        if not isinstance(inspection, str) or not INSPECTION_ID.match(inspection):
            raise RegressionFileError(f"{where}: `inspection` must be an id such as B08")
    else:
        hosted = entry["hosted"]
        if not (
            isinstance(hosted, dict)
            and isinstance(hosted.get("run_id"), str)
            and hosted.get("finding") not in (None, "")
        ):
            raise RegressionFileError(f"{where}: `hosted` needs `run_id` and `finding`")


def local_inspections(entries: list[dict[str, Any]]) -> list[str]:
    """Pinned inspection ids, in file order, without duplicates."""
    return list(dict.fromkeys(e["inspection"] for e in entries if "inspection" in e))


def save(path: str | Path, entries: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"version": VERSION, "regressions": entries}, sort_keys=False, allow_unicode=True
    )
    target.write_text(HEADER + body, encoding="utf-8")


def entry_from_scorecard(
    scorecard: dict[str, Any], inspection: str, *, note: str | None = None, today: date | None = None
) -> dict[str, Any]:
    """A local entry for a failed inspection. Refuses one that did not fail."""
    test = find_test(scorecard, inspection)
    if test is None:
        raise KeyError(f"no inspection {inspection!r} in this report")
    if test.get("status") != FAIL:
        raise ValueError(
            f"{test.get('test_id')} did not fail in this report (status {test.get('status')!r}); "
            "only a failure can be pinned as a regression"
        )
    finding = finding_from_test(test)
    entry: dict[str, Any] = {"inspection": finding.inspection}
    if finding.case_id:
        entry["case"] = finding.case_id
    entry["title"] = finding.name
    entry["added"] = (today or date.today()).isoformat()
    entry["note"] = note or (finding.reason or f"{finding.name} must not fail again.")
    return entry


def add(path: str | Path, entry: dict[str, Any]) -> bool:
    """Append `entry` unless the same inspection or hosted finding is already pinned."""
    validate_entry(entry, "new entry")
    entries = load(path)
    key = entry_key(entry)
    if any(entry_key(existing) == key for existing in entries):
        return False
    entries.append(entry)
    save(path, entries)
    return True


def entry_key(entry: dict[str, Any]) -> tuple[str, ...]:
    if "inspection" in entry:
        return ("inspection", entry["inspection"])
    hosted = entry["hosted"]
    return ("hosted", str(hosted["run_id"]), str(hosted["finding"]))


def check(entries: list[dict[str, Any]], scorecard: dict[str, Any]) -> dict[str, Any]:
    """How the pinned findings fared in this run."""
    checked: list[str] = []
    failed: list[str] = []
    not_run: list[str] = []
    for inspection in local_inspections(entries):
        test = find_test(scorecard, inspection)
        if test is None or test.get("status") not in ("pass", FAIL):
            not_run.append(inspection)
            continue
        checked.append(inspection)
        if test.get("status") == FAIL:
            failed.append(inspection)
    return {
        "file_entries": len(entries),
        "checked": checked,
        "failed": failed,
        "undecided": not_run,
        "hosted": sum(1 for e in entries if "hosted" in e),
    }
