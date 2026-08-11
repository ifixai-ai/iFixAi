"""Roles and tools the engine injects as held-out probes (B08, P01).

These are absent from the fixture on purpose, to check that an unknown
principal gets refused. They flow through the governance hooks like real
traffic, so anything auditing fixture content has to skip them or it
penalises the fixture for the engine's own probe.
"""

HOLDOUT_PREFIX = "ifixai_holdout_"


def is_holdout(identifier: str | None) -> bool:
    return bool(identifier) and str(identifier).startswith(HOLDOUT_PREFIX)
