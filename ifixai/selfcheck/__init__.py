"""Self-check: the free, local run of the open core against an agent URL.

A self-check is a habit, not a verdict anyone else should rely on. Everything
this package produces says so in words: the label is `self-check`,
`certified` is always false, there is never a badge, and every sentence it
writes for a person starts with "Self-check, not certified". Certified results
come only from certified mode, which this package never touches.

Modules:

- `result`: turn a run's JSON scorecard into a self-check result and its
  one-sentence verdict, built from counts by a template.
- `explain`: a plain-language explanation of one failure, from the evidence.
- `regressions`: the `.ifixai/regressions.yaml` file a finding is pinned in.
- `sarif`: SARIF 2.1.0 for GitHub code scanning.
- `comment`: the pull-request comment carrying the plain verdict.
"""

from ifixai.selfcheck.result import (
    CERTIFIED,
    LABEL,
    NOTICE,
    SCHEMA,
    SelfCheckError,
    build_result,
    load_scorecard,
)

__all__ = [
    "CERTIFIED",
    "LABEL",
    "NOTICE",
    "SCHEMA",
    "SelfCheckError",
    "build_result",
    "load_scorecard",
]
