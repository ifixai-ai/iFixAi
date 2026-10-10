import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import yaml

B14_DEFAULT_SEED: Final[int] = 20260422
B28_DEFAULT_SEED: Final[int] = 20260422
B30_DEFAULT_SEED: Final[int] = 20260422

_OPERATOR_ORDER: Final[tuple[str, ...]] = (
    "case_flip",
    "punctuation_jitter",
    "filler_word_insert",
    "synonym_substitute",
)


class AdversarialMutatorError(Exception):
    pass


@dataclass(frozen=True)
class SeedEntry:
    id: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MutatedVariant:
    seed_id: str
    variant_index: int
    original_text: str
    mutated_text: str
    operators_applied: tuple[str, ...]


@dataclass(frozen=True)
class SynonymBank:
    filler_words: tuple[str, ...]
    synonym_groups: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class _MutationContext:
    rng: random.Random
    bank: SynonymBank


def load_seeds(path: Path) -> list[SeedEntry]:
    if not path.exists():
        raise AdversarialMutatorError(f"seed corpus not found at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AdversarialMutatorError(f"corpus YAML parse failed: {exc}") from exc
    if not isinstance(raw, dict):
        raise AdversarialMutatorError("corpus YAML must be a mapping at the top level")
    entries = raw.get("seeds")
    if not isinstance(entries, list) or not entries:
        raise AdversarialMutatorError("corpus YAML must declare a non-empty 'seeds' list")

    seeds: list[SeedEntry] = []
    seen_ids: set[str] = set()
    for entry in entries:
        seeds.append(_parse_seed_entry(entry, seen_ids))
    seeds.sort(key=lambda seed: seed.id)
    return seeds


def _parse_seed_entry(entry: object, seen_ids: set[str]) -> SeedEntry:
    if not isinstance(entry, dict):
        raise AdversarialMutatorError(f"seed entry must be a mapping: {entry!r}")
    seed_id = entry.get("id")
    if not isinstance(seed_id, str) or not seed_id.strip():
        raise AdversarialMutatorError(f"seed entry missing 'id': {entry!r}")
    seed_id = seed_id.strip()
    if seed_id in seen_ids:
        raise AdversarialMutatorError(f"duplicate seed id: {seed_id!r}")
    seen_ids.add(seed_id)
    text = _extract_seed_text(entry)
    metadata = _extract_seed_metadata(entry)
    return SeedEntry(id=seed_id, text=text, metadata=metadata)


def _extract_seed_text(entry: dict[str, object]) -> str:
    if "rule_text" in entry and isinstance(entry["rule_text"], str):
        return entry["rule_text"].strip()
    legitimate = entry.get("legitimate_request")
    covert = entry.get("covert_objective")
    if isinstance(legitimate, str) and isinstance(covert, str):
        return f"{legitimate.strip()}. Also, {covert.strip()}."
    # `b28_prompt` key carries verbatim multi-line prompts for B28 RAG injection cases.
    # Named specifically to prevent B14/B30 corpus authors from accidentally routing
    # arbitrary content through the verbatim injection path.
    prompt = entry.get("b28_prompt")
    if isinstance(prompt, str):
        return prompt.strip()
    text = entry.get("text")
    if isinstance(text, str):
        return text.strip()
    raise AdversarialMutatorError(
        f"seed entry {entry.get('id')!r} must carry either 'rule_text', "
        f"'b28_prompt', 'text', or both 'legitimate_request' and 'covert_objective'"
    )


def _extract_seed_metadata(entry: dict[str, object]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key in (
        "severity", "target_marker", "legitimate_request", "covert_objective",
        "expected_outcome", "expected_behavior",
    ):
        value = entry.get(key)
        if isinstance(value, str):
            metadata[key] = value.strip()
    return metadata


def load_synonym_bank(path: Path) -> SynonymBank:
    if not path.exists():
        raise AdversarialMutatorError(f"synonym bank not found at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AdversarialMutatorError(f"synonym YAML parse failed: {exc}") from exc
    if not isinstance(raw, dict):
        raise AdversarialMutatorError("synonym YAML must be a mapping at the top level")

    fillers_raw = raw.get("filler_words", [])
    if not isinstance(fillers_raw, list) or not all(isinstance(w, str) for w in fillers_raw):
        raise AdversarialMutatorError("'filler_words' must be a list of strings")
    synonyms_raw = raw.get("synonyms", [])
    if not isinstance(synonyms_raw, list):
        raise AdversarialMutatorError("'synonyms' must be a list of synonym groups")
    groups: list[tuple[str, ...]] = []
    for group in synonyms_raw:
        if not isinstance(group, list) or not all(isinstance(w, str) for w in group):
            raise AdversarialMutatorError("each synonym group must be a list of strings")
        if len(group) >= 2:
            groups.append(tuple(group))
    return SynonymBank(
        filler_words=tuple(fillers_raw),
        synonym_groups=tuple(groups),
    )


def expand_seeds(
    seeds: list[SeedEntry],
    synonyms_path: Path,
    seed: int,
    variants_per_seed: int = 3,
) -> list[MutatedVariant]:
    if variants_per_seed <= 0:
        return []
    bank = load_synonym_bank(synonyms_path)
    sorted_seeds = sorted(seeds, key=lambda entry: entry.id)
    rng = random.Random(seed)
    variants: list[MutatedVariant] = []
    for seed_entry in sorted_seeds:
        for variant_index in range(variants_per_seed):
            variants.append(_build_variant(seed_entry, variant_index, rng, bank))
    return variants


def _build_variant(
    seed_entry: SeedEntry,
    variant_index: int,
    rng: random.Random,
    bank: SynonymBank,
) -> MutatedVariant:
    context = _MutationContext(rng=rng, bank=bank)
    mutated = seed_entry.text
    operators_applied: list[str] = []
    for operator_name in _OPERATOR_ORDER:
        if not rng.random() < 0.75:
            continue
        new_text = _apply_operator(operator_name, mutated, context)
        if new_text != mutated:
            mutated = new_text
            operators_applied.append(operator_name)
    return MutatedVariant(
        seed_id=seed_entry.id,
        variant_index=variant_index,
        original_text=seed_entry.text,
        mutated_text=mutated,
        operators_applied=tuple(operators_applied),
    )


def _apply_operator(operator_name: str, text: str, context: _MutationContext) -> str:
    if operator_name == "case_flip":
        return _apply_case_flip(text, context.rng)
    if operator_name == "punctuation_jitter":
        return _apply_punctuation_jitter(text, context.rng)
    if operator_name == "filler_word_insert":
        return _apply_filler_insert(text, context.rng, context.bank.filler_words)
    if operator_name == "synonym_substitute":
        return _apply_synonym_substitute(text, context.rng, context.bank.synonym_groups)
    raise AdversarialMutatorError(f"unknown operator: {operator_name!r}")


def _apply_case_flip(text: str, rng: random.Random) -> str:
    letter_positions = [i for i, ch in enumerate(text) if ch.isalpha()]
    if not letter_positions:
        return text
    flip_count = max(1, len(letter_positions) // 10)
    targets = set(rng.sample(letter_positions, min(flip_count, len(letter_positions))))
    chars = list(text)
    for i in targets:
        chars[i] = chars[i].swapcase()
    return "".join(chars)


def _apply_punctuation_jitter(text: str, rng: random.Random) -> str:
    if not text:
        return text
    choice = rng.randint(0, 2)
    if choice == 0 and not text.endswith(('.', '!', '?')):
        return text + "."
    if choice == 1:
        return text + "!"
    comma_positions = [i for i, ch in enumerate(text) if ch == " "]
    if not comma_positions:
        return text
    pos = rng.choice(comma_positions)
    return text[:pos] + "," + text[pos:]


def _apply_filler_insert(text: str, rng: random.Random, fillers: tuple[str, ...]) -> str:
    if not fillers or not text:
        return text
    filler = rng.choice(fillers)
    space_positions = [i for i, ch in enumerate(text) if ch == " "]
    if not space_positions:
        return f"{filler}, {text}"
    pos = rng.choice(space_positions)
    return text[:pos] + f" {filler}" + text[pos:]


def _apply_synonym_substitute(
    text: str,
    rng: random.Random,
    groups: tuple[tuple[str, ...], ...],
) -> str:
    if not groups:
        return text
    lower_text = text.lower()
    candidates: list[tuple[int, int, tuple[str, ...], str]] = []
    for group in groups:
        for word in group:
            for match in re.finditer(re.escape(word), lower_text):
                idx = match.start()
                before = idx == 0 or not lower_text[idx - 1].isalpha()
                after_idx = match.end()
                after = after_idx == len(lower_text) or not lower_text[after_idx].isalpha()
                if before and after:
                    candidates.append((idx, len(word), group, word))
                    # Preserve the previous one-candidate-per-word RNG policy.
                    break
    if not candidates:
        return text
    start, length, group, original_word = rng.choice(candidates)
    replacement_pool = tuple(w for w in group if w != original_word)
    if not replacement_pool:
        return text
    replacement = rng.choice(replacement_pool)
    return text[:start] + replacement + text[start + length:]
