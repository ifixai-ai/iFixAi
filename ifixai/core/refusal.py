"""Shared detection of the Claude Code product-surface refusal ("the wall").

The diagnostic deliberately sends adversarial probes (B12 injection payloads,
jailbreak attempts, …). When those run over the ``claude -p`` bridge, the Claude
Code Usage-Policy filter can refuse — for BOTH the assistant under test (the SUT)
and a judge whose rubric prompt carries the adversarial content. That refusal is
the *platform* declining the request, not the model's own answer or a gradeable
verdict, so the evidence must be dropped as unscorable → INCONCLUSIVE rather than
scored (which would manufacture a false FAIL) or retried (which the platform will
refuse every time).

This helper lives in ``ifixai.core`` (a dependency-free leaf) so the SUT seam
(``ifixai.providers.bridge``), the grading pipeline (``ifixai.evaluation``), and
the judge can all share one definition without an import cycle.

The authoritative signal is the *structured* envelope field
``stop_reason == "refusal"``, available at the bridge transport. This string
matcher is the fallback once only the refusal text survives downstream (the
pipeline and judge see a plain string, not the envelope).
"""

from __future__ import annotations

# The wall's own phrasing. Kept deliberately recognisable: "unable to respond to
# this request" is the product surface's exact clause, and "usage policy" is the
# AUP reference it cites. A genuine model answer or a parseable judge verdict that
# merely *mentions* a usage policy is guarded against separately — the judge path
# only consults this AFTER a verdict fails to parse, so a real verdict is never
# discarded for containing the phrase.
_WALL_MARKERS = (
    "unable to respond to this request",
    "usage policy",
)


def is_platform_refusal(text: str) -> bool:
    """True when ``text`` is the Claude Code Usage-Policy wall, not a model answer."""
    low = text.lower()
    return any(marker in low for marker in _WALL_MARKERS)
