import random
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class PromptPoolError(Exception):
    """Raised when a prompt-pool YAML is missing, malformed, or invalid."""


class PhrasingGroup(BaseModel):
    """One equivalence class of semantically identical phrasings (B29)."""

    model_config = {"frozen": True}

    id: str
    category: str
    phrasings: list[str] = Field(default_factory=list)


class PromptEntry(BaseModel):
    """A single prompt string with a stable ID (B32)."""

    model_config = {"frozen": True}

    id: str
    text: str


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_phrasing_pool(path: Path) -> list[PhrasingGroup]:
    """Load and validate a B29 phrasing-group YAML."""
    raw = _read_yaml(path)

    groups_raw = raw.get("groups")
    if not isinstance(groups_raw, list):
        raise PromptPoolError(f"{path}: top-level 'groups' list is required")

    groups: list[PhrasingGroup] = []
    seen_ids: set[str] = set()
    for entry in groups_raw:
        if not isinstance(entry, dict):
            raise PromptPoolError(
                f"{path}: each group entry must be a mapping, got {entry!r}"
            )
        gid = _require_str(entry, "id", path)
        category = _require_str(entry, "category", path)
        phrasings = entry.get("phrasings")
        if not isinstance(phrasings, list) or len(phrasings) < 2:
            raise PromptPoolError(
                f"{path}: group {gid!r} must have a 'phrasings' list with at least 2 entries"
            )
        if any(not isinstance(text, str) or not text.strip() for text in phrasings):
            raise PromptPoolError(
                f"{path}: group {gid!r} phrasings must be nonempty strings"
            )
        if gid in seen_ids:
            raise PromptPoolError(f"{path}: duplicate group id {gid!r}")
        seen_ids.add(gid)
        groups.append(
            PhrasingGroup(id=gid, category=category, phrasings=list(phrasings))
        )

    if not groups:
        raise PromptPoolError(f"{path}: declares zero phrasing groups")
    return groups


def load_prompt_pool(path: Path) -> list[PromptEntry]:
    """Load and validate a B32 off-topic prompt YAML."""
    raw = _read_yaml(path)

    prompts_raw = raw.get("prompts")
    if not isinstance(prompts_raw, list):
        raise PromptPoolError(f"{path}: top-level 'prompts' list is required")

    entries: list[PromptEntry] = []
    seen_ids: set[str] = set()
    for entry in prompts_raw:
        if not isinstance(entry, dict):
            raise PromptPoolError(
                f"{path}: each prompt entry must be a mapping, got {entry!r}"
            )
        eid = _require_str(entry, "id", path)
        text = _require_str(entry, "text", path)
        if not text.strip():
            raise PromptPoolError(f"{path}: prompt {eid!r} has empty text")
        if eid in seen_ids:
            raise PromptPoolError(f"{path}: duplicate prompt id {eid!r}")
        seen_ids.add(eid)
        entries.append(PromptEntry(id=eid, text=text))

    if not entries:
        raise PromptPoolError(f"{path}: declares zero prompt entries")
    return entries


# ---------------------------------------------------------------------------
# Samplers
# ---------------------------------------------------------------------------


def sample_phrasing_pool(
    pool: list[PhrasingGroup],
    n: int,
    seed: int,
    category: str | None = None,
) -> list[PhrasingGroup]:
    """Return up to *n* phrasing groups sampled without replacement.

    When *category* is given only groups in that category are eligible.
    Sampling is deterministic for a fixed *seed*.
    """
    candidates = [g for g in pool if category is None or g.category == category]
    if not candidates:
        return []
    rng = random.Random(seed)
    take = min(n, len(candidates))
    return rng.sample(candidates, take)


def sample_prompt_pool(
    pool: list[PromptEntry],
    n: int,
    seed: int,
) -> list[PromptEntry]:
    """Return exactly *n* prompt entries sampled without replacement.

    If the pool has fewer than *n* entries returns all of them.
    Sampling is deterministic for a fixed *seed*.
    """
    rng = random.Random(seed)
    take = min(n, len(pool))
    return rng.sample(pool, take)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise PromptPoolError(f"prompt pool not found: {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise PromptPoolError(f"{path}: YAML parse error: {exc}") from exc
    if not isinstance(raw, dict):
        raise PromptPoolError(f"{path}: top-level value must be a mapping")
    return raw


def _require_str(entry: dict, field: str, path: Path) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PromptPoolError(
            f"{path}: entry missing required string field {field!r}: {entry!r}"
        )
    return value.strip()


def derive_secondary_seed(seed: int) -> int:
    """Produce a second independent seed from *seed* without XOR hacks.

    ``random.Random.randrange(stop)`` returns ``0 <= n < stop``, deterministic
    from *seed*. Replaces the broken ``randbelow`` call (that method lives on
    ``secrets.SystemRandom``, not on ``random.Random``).
    """
    return random.Random(seed).randrange(2**31)
