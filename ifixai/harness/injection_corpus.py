import random
import secrets
from collections import defaultdict
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, Field

_CORPUS_PATH = Path(__file__).parent.parent / "inspections" / "b12_prompt_injection" / "corpus.yaml"

DEFAULT_INJECTION_SEED: Final[int] = 20260422


class InjectionPayload(BaseModel):
    pass

    model_config = {"frozen": True}

    id: str
    category: str
    payload: str


class InjectionCorpus(BaseModel):
    pass

    model_config = {"frozen": True}

    version: int
    sample_per_category: int = 3
    payloads: list[InjectionPayload] = Field(default_factory=list)

    def categories(self) -> list[str]:
        seen: list[str] = []
        for p in self.payloads:
            if p.category not in seen:
                seen.append(p.category)
        return seen

    def by_category(self) -> dict[str, list[InjectionPayload]]:
        out: dict[str, list[InjectionPayload]] = defaultdict(list)
        for p in self.payloads:
            out[p.category].append(p)
        return dict(out)


class InjectionCorpusError(Exception):
    pass


def load_injection_corpus(path: Path | None = None) -> InjectionCorpus:
    yaml_path = path or _CORPUS_PATH
    if not yaml_path.exists():
        raise InjectionCorpusError(f"injection corpus not found at {yaml_path}")
    try:
        with open(yaml_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise InjectionCorpusError(f"corpus YAML parse failed: {exc}") from exc

    if not isinstance(raw, dict):
        raise InjectionCorpusError("corpus YAML must be a mapping at the top level")
    if "payloads" not in raw or not isinstance(raw["payloads"], list):
        raise InjectionCorpusError("corpus YAML must declare a 'payloads' list")

    payloads: list[InjectionPayload] = []
    seen_ids: set[str] = set()
    for entry in raw["payloads"]:
        if not isinstance(entry, dict):
            raise InjectionCorpusError(f"payload entry must be a mapping: {entry!r}")
        for field in ("id", "category", "payload"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                raise InjectionCorpusError(
                    f"payload entry missing required field {field!r}: {entry!r}"
                )
        payload_id = entry["id"].strip()
        if payload_id in seen_ids:
            raise InjectionCorpusError(f"duplicate payload id: {payload_id!r}")
        seen_ids.add(payload_id)
        payloads.append(
            InjectionPayload(
                id=payload_id,
                category=entry["category"].strip(),
                payload=entry["payload"],
            )
        )

    if not payloads:
        raise InjectionCorpusError("corpus declares zero payloads")

    return InjectionCorpus(
        version=int(raw.get("version", 1)),
        sample_per_category=int(raw.get("sample_per_category", 3)),
        payloads=payloads,
    )


def sample_corpus(
    corpus: InjectionCorpus,
    per_category: int | None = None,
    *,
    seed: int | None = None,
) -> list[InjectionPayload]:
    n = per_category if per_category is not None else corpus.sample_per_category
    if n <= 0:
        return []
    rng = random.Random(seed if seed is not None else secrets.randbelow(2**31))
    grouped = corpus.by_category()
    out: list[InjectionPayload] = []
    for category in corpus.categories():
        group = grouped[category]
        take = min(n, len(group))
        out.extend(rng.sample(group, take))
    return out
