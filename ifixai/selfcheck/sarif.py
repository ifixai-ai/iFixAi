"""SARIF 2.1.0 from a scorecard, so findings show up in GitHub code scanning.

One rule per inspection in the run, one result per failed inspection. Agent
behaviour has no line of source code, so every result points at the file that
defines what was tested (the fixture, `ifixai.yaml`, or the regressions file)
at line 1; code scanning needs a location in the repository to show a result.

Each result's message starts with the self-check label, and the run's
properties carry `certified: false`, so a finding in the Security tab can
never be read as a certified result.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ifixai._version import VERSION
from ifixai.selfcheck.explain import clip
from ifixai.selfcheck.result import (
    CERTIFIED,
    FAIL,
    LABEL,
    NOTICE,
    VERDICT_PREFIX,
    finding_from_test,
)

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"
TOOL_NAME = "iFixAi self-check"
INFORMATION_URI = "https://ifixai.ai"
ANCHOR_CANDIDATES = ("ifixai.yaml", ".ifixai/regressions.yaml")
FALLBACK_ANCHOR = "README.md"

# GitHub orders alerts by this number (0.0-10.0). Categories that let an agent
# act beyond its authority rank highest; anything unlisted is medium.
SEVERITY_BY_CATEGORY = {
    "MANIPULATION": "8.0",
    "DECEPTION": "7.5",
    "FABRICATION": "7.0",
    "UNPREDICTABILITY": "5.0",
    "OPACITY": "4.0",
}


def choose_anchor(root: Path, fixture: str | None = None) -> str:
    """The repository-relative file each result points at."""
    if fixture:
        candidate = Path(fixture)
        try:
            relative = candidate.resolve().relative_to(root.resolve())
        except ValueError:
            relative = None
        if relative is not None and (root / relative).is_file():
            return relative.as_posix()
    for name in ANCHOR_CANDIDATES:
        if (root / name).is_file():
            return name
    return FALLBACK_ANCHOR


def rule_for(test: dict[str, Any]) -> dict[str, Any]:
    inspection = str(test.get("test_id", ""))
    name = str(test.get("name") or inspection)
    category = str(test.get("category") or "")
    controls = [
        f"{m.get('framework')} {m.get('control_id')}"
        for m in test.get("regulatory_mappings") or []
        if isinstance(m, dict) and m.get("framework") and m.get("control_id")
    ]
    help_text = f"{name}. Category: {category.lower().replace('_', ' ')}."
    if controls:
        help_text += " Maps to " + "; ".join(controls[:6]) + "."
    return {
        "id": inspection,
        "name": "".join(part.capitalize() for part in name.replace("-", " ").split()) or inspection,
        "shortDescription": {"text": name},
        "fullDescription": {"text": help_text},
        "help": {"text": f"{help_text} {NOTICE}"},
        "defaultConfiguration": {"level": "error"},
        "properties": {
            "tags": ["ai-agent", "self-check", category.lower()] if category else ["ai-agent", "self-check"],
            "security-severity": SEVERITY_BY_CATEGORY.get(category.upper(), "6.0"),
        },
    }


def fingerprint(inspection: str, case_id: str | None, target: str | None) -> str:
    """Stable across runs, so the same finding is one alert, not one per run."""
    material = "\x1f".join([inspection, case_id or "", target or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def result_for(test: dict[str, Any], rule_index: int, anchor: str, target: str | None) -> dict[str, Any]:
    finding = finding_from_test(test)
    parts = [f"{VERDICT_PREFIX} {finding.name} failed"]
    if finding.score is not None and finding.threshold is not None:
        parts[0] += f" ({finding.score:.0%}, needs {finding.threshold:.0%})"
    parts[0] += "."
    actual = clip(finding.actual, 300)
    if actual:
        parts.append(f"The agent replied: \"{actual}\"")
    if finding.reason:
        parts.append(clip(finding.reason, 300) or "")
    parts.append(f"Explain: ifixai findings explain <report> {finding.inspection}")
    return {
        "ruleId": finding.inspection,
        "ruleIndex": rule_index,
        "level": "error",
        "message": {"text": " ".join(p for p in parts if p)},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": anchor, "uriBaseId": "%SRCROOT%"},
                    "region": {"startLine": 1},
                }
            }
        ],
        "partialFingerprints": {
            "ifixaiFinding/v1": fingerprint(finding.inspection, finding.case_id, target)
        },
        "properties": {"label": LABEL, "certified": CERTIFIED, "case_id": finding.case_id},
    }


def to_sarif(
    scorecard: dict[str, Any],
    *,
    anchor: str = FALLBACK_ANCHOR,
    target: str | None = None,
    verdict: str | None = None,
) -> dict[str, Any]:
    tests = [t for t in scorecard.get("test_results") or [] if isinstance(t, dict)]
    rules = [rule_for(t) for t in tests]
    index = {rule["id"]: i for i, rule in enumerate(rules)}
    results = [
        result_for(t, index[str(t.get("test_id", ""))], anchor, target)
        for t in tests
        if t.get("status") == FAIL
    ]
    metadata = scorecard.get("metadata") or {}
    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": VERSION,
                        "semanticVersion": VERSION,
                        "informationUri": INFORMATION_URI,
                        "rules": rules,
                    }
                },
                # A category with a trailing slash: GitHub keeps one alert set per category.
                "automationDetails": {"id": "ifixai/self-check/"},
                "results": results,
                "properties": {
                    "label": LABEL,
                    "certified": CERTIFIED,
                    "badge": None,
                    "notice": NOTICE,
                    "verdict": verdict,
                    "specification_version": metadata.get("specification_version"),
                    "scoring_composition_fingerprint": metadata.get(
                        "scoring_composition_fingerprint"
                    ),
                },
            }
        ],
    }
