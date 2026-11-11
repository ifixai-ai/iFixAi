"""The self-check result: what a free local run found, labelled as exactly that.

Input is the JSON scorecard `ifixai run --format json` writes. Output is a
small, stable document (`ifixai.self-check/1`) that the CLI prints, the MCP
server returns, and SARIF and the PR comment are built from.

The verdict sentence is a template filled from counts. No model writes it, and
it never uses the words a certified result would ("passed", "verified"):
a self-check that found nothing says "no failures found", because that is all
it can honestly say.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SCHEMA = "ifixai.self-check/1"
LABEL = "self-check"
CERTIFIED = False
NOTICE = (
    "Self-check, not certified. A self-check is a free local run of the open "
    "core. It carries no badge and is not an iFixAi audit result. Certified "
    "results come only from iFixAi certified mode."
)
VERDICT_PREFIX = "Self-check, not certified:"

# Statuses as the scorecard writes them. Anything else is reported as-is and
# counted as undecided, never as a pass.
FAIL = "fail"
PASS = "pass"
UNDECIDED = ("inconclusive", "error", "skipped", "not_applicable")

MAX_SCORECARD_BYTES = 64 * 1024 * 1024


class SelfCheckError(ValueError):
    """The scorecard could not be read as one."""


def load_scorecard(path: str | Path) -> dict[str, Any]:
    """Read a run's JSON scorecard, refusing anything that is not one."""
    source = Path(path)
    try:
        with source.open("rb") as handle:
            data = handle.read(MAX_SCORECARD_BYTES + 1)
    except OSError as exc:
        raise SelfCheckError(f"cannot read scorecard {source}: {exc.strerror}") from None
    if len(data) > MAX_SCORECARD_BYTES:
        raise SelfCheckError(f"scorecard {source} is larger than 64 MiB")
    try:
        scorecard = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelfCheckError(f"scorecard {source} is not JSON: {exc}") from None
    if not isinstance(scorecard, dict) or not isinstance(scorecard.get("test_results"), list):
        raise SelfCheckError(f"{source} is not an ifixai scorecard (no test_results)")
    return scorecard


def safe_target(url: str | None) -> str | None:
    """The agent URL without credentials, query or fragment, safe to print and store."""
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


@dataclass(frozen=True)
class Finding:
    """One inspection that failed, with the first piece of evidence that shows it."""

    inspection: str
    name: str
    category: str
    status: str
    score: float | None
    threshold: float | None
    case_id: str | None
    asked: str | None
    expected: str | None
    actual: str | None
    reason: str | None
    mappings: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "inspection": self.inspection,
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "score": self.score,
            "threshold": self.threshold,
            "case_id": self.case_id,
            "asked": self.asked,
            "expected": self.expected,
            "actual": self.actual,
            "reason": self.reason,
            "mappings": [dict(m) for m in self.mappings],
        }


def failing_evidence(test: dict[str, Any]) -> dict[str, Any] | None:
    """The first evidence item that did not pass, or None when there is none."""
    for item in test.get("evidence") or []:
        if isinstance(item, dict) and item.get("passed") is False:
            return item
    return None


def failure_reason(item: dict[str, Any] | None) -> str | None:
    """Why the evidence item failed, in the scorecard's own words."""
    if not item:
        return None
    for dimension in item.get("dimension_scores") or []:
        if isinstance(dimension, dict) and dimension.get("passed") is False:
            reasoning = str(dimension.get("reasoning") or "").strip()
            name = str(dimension.get("dimension_name") or "").replace("_", " ")
            if reasoning:
                return f"{name}: {reasoning}" if name else reasoning
    result = str(item.get("evaluation_result") or "").strip()
    return result or None


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def finding_from_test(test: dict[str, Any]) -> Finding:
    item = failing_evidence(test)
    mappings = tuple(
        {
            "framework": str(m.get("framework", "")),
            "control_id": str(m.get("control_id", "")),
            "control_name": str(m.get("control_name", "")),
        }
        for m in test.get("regulatory_mappings") or []
        if isinstance(m, dict)
    )
    return Finding(
        inspection=str(test.get("test_id", "")),
        name=str(test.get("name") or test.get("test_id") or ""),
        category=str(test.get("category") or ""),
        status=str(test.get("status") or ""),
        score=test.get("score"),
        threshold=test.get("threshold"),
        case_id=text_or_none(item.get("test_case_id")) if item else None,
        asked=text_or_none(item.get("prompt_sent")) if item else None,
        expected=text_or_none(item.get("expected")) if item else None,
        actual=text_or_none(item.get("actual")) if item else None,
        reason=failure_reason(item),
        mappings=mappings,
    )


def find_test(scorecard: dict[str, Any], inspection: str) -> dict[str, Any] | None:
    wanted = inspection.strip().upper()
    for test in scorecard.get("test_results") or []:
        if isinstance(test, dict) and str(test.get("test_id", "")).upper() == wanted:
            return test
    return None


def status_counts(scorecard: dict[str, Any]) -> dict[str, int]:
    counts = {"pass": 0, "fail": 0, "undecided": 0, "total": 0}
    for test in scorecard.get("test_results") or []:
        if not isinstance(test, dict):
            continue
        status = str(test.get("status") or "")
        counts["total"] += 1
        if status == PASS:
            counts["pass"] += 1
        elif status == FAIL:
            counts["fail"] += 1
        else:
            counts["undecided"] += 1
    return counts


def run_date(scorecard: dict[str, Any]) -> str | None:
    raw = (scorecard.get("metadata") or {}).get("evaluation_date")
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    # `%-d` is glibc-only; build the day by hand so Windows prints it too.
    return f"{moment.day} {moment:%b %Y}"


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def verdict_sentence(
    counts: dict[str, int], failed_names: list[str], date: str | None, suite: str | None
) -> str:
    """One sentence, from counts, by template. Never "passed", never "verified"."""
    when = f" on {date}" if date else ""
    scope = f" ({suite} suite)" if suite else ""
    total, failed, undecided = counts["total"], counts["fail"], counts["undecided"]
    if total == 0:
        return f"{VERDICT_PREFIX} nothing was checked{when}{scope}."
    if failed:
        names = ", ".join(failed_names[:3])
        more = f" and {plural(len(failed_names) - 3, 'other')}" if len(failed_names) > 3 else ""
        return (
            f"{VERDICT_PREFIX} {failed} of {plural(total, 'check')} failed{when}{scope}: "
            f"{names}{more}."
        )
    if undecided == total:
        return (
            f"{VERDICT_PREFIX} no verdict{when}{scope}; none of the "
            f"{plural(total, 'check')} could be decided."
        )
    tail = f"; {undecided} could not be decided" if undecided else ""
    return f"{VERDICT_PREFIX} no failures found in {plural(total, 'check')}{when}{scope}{tail}."


def build_result(
    scorecard: dict[str, Any],
    *,
    target: str | None = None,
    suite: str | None = None,
    report_path: str | None = None,
    regressions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The `ifixai.self-check/1` document for a scorecard."""
    metadata = scorecard.get("metadata") or {}
    judge_stats = metadata.get("judge_stats") or {}
    findings = [
        finding_from_test(test)
        for test in scorecard.get("test_results") or []
        if isinstance(test, dict) and test.get("status") == FAIL
    ]
    undecided = [
        {"inspection": str(t.get("test_id", "")), "name": str(t.get("name") or ""),
         "status": str(t.get("status") or "")}
        for t in scorecard.get("test_results") or []
        if isinstance(t, dict) and t.get("status") not in (PASS, FAIL)
    ]
    counts = status_counts(scorecard)
    return {
        "schema": SCHEMA,
        "label": LABEL,
        "certified": CERTIFIED,
        "badge": None,
        "notice": NOTICE,
        "verdict": verdict_sentence(
            counts, [f.name for f in findings], run_date(scorecard), suite
        ),
        "target": safe_target(target),
        "suite": suite,
        "date": metadata.get("evaluation_date"),
        "engine": {
            "specification_version": metadata.get("specification_version"),
            "scoring_composition_version": metadata.get("scoring_composition_version"),
            "scoring_composition_fingerprint": metadata.get("scoring_composition_fingerprint"),
        },
        "judge": {
            "self_judged": bool(metadata.get("self_judged")),
            "provider": judge_stats.get("judge_provider"),
            "model": judge_stats.get("judge_model"),
        },
        "counts": counts,
        "findings": [f.as_dict() for f in findings],
        "undecided": undecided,
        "regressions": regressions,
        "report_path": report_path,
    }
