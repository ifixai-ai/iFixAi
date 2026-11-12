"""Explain one failure in plain language, from the evidence alone.

A template, not a model: every sentence is filled from fields the scorecard
already holds (what was asked, what was expected, what the agent did, which
check failed and why, which framework controls it maps to). If a field is
missing the sentence is left out rather than guessed.
"""

from __future__ import annotations

from typing import Any

from ifixai.selfcheck.result import (
    FAIL,
    NOTICE,
    Finding,
    find_test,
    finding_from_test,
)

QUOTE_LIMIT = 600


def clip(text: str | None, limit: int = QUOTE_LIMIT) -> str | None:
    if text is None:
        return None
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


EXPECTATIONS = {
    "refuse": "decline the request",
    "refusal": "decline the request",
    "comply": "carry out the request",
    "escalate": "hand the request to a person",
    "clarify": "ask a clarifying question",
}


def expectation_phrase(expected: str | None) -> str | None:
    if not expected:
        return None
    return EXPECTATIONS.get(expected.strip().lower(), expected.strip())


def explanation_lines(finding: Finding) -> list[str]:
    lines = [f"What failed: {finding.name} ({finding.inspection}), in {finding.category.lower().replace('_', ' ') or 'an uncategorised area'}."]
    if finding.score is not None and finding.threshold is not None:
        lines.append(
            f"Score: {finding.score:.0%} against a required {finding.threshold:.0%}."
        )
    asked = clip(finding.asked)
    if asked:
        lines.append(f"What the agent was asked: \"{asked}\"")
    expected = expectation_phrase(finding.expected)
    if expected:
        lines.append(f"What it should have done: {expected}.")
    actual = clip(finding.actual)
    if actual:
        lines.append(f"What it did instead: \"{actual}\"")
    if finding.reason:
        lines.append(f"Why that counts as a failure: {clip(finding.reason, 400)}")
    controls = [
        f"{m['framework']} {m['control_id']} ({m['control_name']})".strip()
        for m in finding.mappings
        if m.get("framework") and m.get("control_id")
    ]
    if controls:
        lines.append("Maps to: " + "; ".join(controls[:4]) + ".")
    lines.append(
        "Next step: fix the behaviour, then pin this finding so it cannot come back "
        f"(`ifixai findings add-regression <report> {finding.inspection}`) and re-run "
        "`ifixai audit`."
    )
    return lines


def explain(scorecard: dict[str, Any], inspection: str) -> dict[str, Any]:
    """Explanation document for one inspection in a scorecard.

    Raises KeyError when the scorecard has no such inspection. An inspection
    that did not fail is explained as such rather than refused, so a caller
    asking "why did B08 fail" learns that it did not.
    """
    test = find_test(scorecard, inspection)
    if test is None:
        raise KeyError(f"no inspection {inspection!r} in this report")
    finding = finding_from_test(test)
    if test.get("status") != FAIL:
        text = (
            f"{finding.name} ({finding.inspection}) did not fail in this run; "
            f"its status is {finding.status or 'unknown'}. There is no failure to explain."
        )
        return {"inspection": finding.inspection, "failed": False, "text": text, "notice": NOTICE}
    lines = explanation_lines(finding)
    return {
        "inspection": finding.inspection,
        "failed": True,
        "finding": finding.as_dict(),
        "lines": lines,
        "text": "\n".join(lines),
        "notice": NOTICE,
    }
