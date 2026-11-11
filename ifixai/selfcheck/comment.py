"""The pull-request comment: the plain verdict, the label, and what failed.

The first line is a hidden marker so a CI step can find and update its own
comment instead of adding a new one on every push.
"""

from __future__ import annotations

from typing import Any

from ifixai.selfcheck.explain import clip

MARKER = "<!-- ifixai-self-check -->"
MAX_LISTED = 10


def escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(result: dict[str, Any]) -> str:
    lines = [MARKER, "### iFixAi self-check", "", f"**{result['verdict']}**", ""]
    findings = result.get("findings") or []
    if findings:
        lines += ["| Check | Area | What the agent did |", "|---|---|---|"]
        for finding in findings[:MAX_LISTED]:
            did = clip(finding.get("actual") or finding.get("reason") or "", 140) or "see report"
            lines.append(
                f"| {escape_cell(finding['name'])} (`{finding['inspection']}`) "
                f"| {escape_cell(finding.get('category', '').lower().replace('_', ' '))} "
                f"| {escape_cell(did)} |"
            )
        if len(findings) > MAX_LISTED:
            lines.append(f"| … and {len(findings) - MAX_LISTED} more | | |")
        lines.append("")
    regressions = result.get("regressions") or {}
    if regressions.get("failed"):
        lines.append(
            "Pinned regressions that failed again: "
            + ", ".join(f"`{i}`" for i in regressions["failed"])
            + "."
        )
        lines.append("")
    elif regressions.get("checked"):
        lines.append(f"Pinned regressions checked: {len(regressions['checked'])}, none failed again.")
        lines.append("")
    lines.append(f"> {result['notice']}")
    return "\n".join(lines) + "\n"
