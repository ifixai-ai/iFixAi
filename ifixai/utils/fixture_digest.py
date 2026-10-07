from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


def _canonicalise(obj: Any) -> Any:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        canonical = {}
        for key, value in obj.items():
            canonical_key = key.isoformat() if isinstance(key, (date, datetime)) else key
            if canonical_key in canonical:
                raise ValueError(f"duplicate canonical fixture key: {canonical_key!r}")
            canonical[canonical_key] = _canonicalise(value)
        return {key: canonical[key] for key in sorted(canonical)}
    if isinstance(obj, list):
        return [_canonicalise(item) for item in obj]
    return obj


def compute_fixture_digest(fixture_path: Path | str) -> str:
    path = Path(fixture_path)
    raw = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    canonical = _canonicalise(parsed)
    serialised = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def verify_fixture_digest(fixture_path: Path | str, expected: str) -> bool:
    return compute_fixture_digest(fixture_path) == expected
