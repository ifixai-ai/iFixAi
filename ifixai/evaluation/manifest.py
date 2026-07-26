

import hashlib
import json
import logging
import re
import secrets
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ifixai.core.types import RunMode
from ifixai.evaluation.types import ModelDescriptor

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO_SHA256 = "0" * 64
_RUN_NONCE_RE = re.compile(r"^[0-9a-f]{16}$")
_EMPTY_RUN_NONCE = ""
CURRENT_MANIFEST_SCHEMA_VERSION = 3
LEGACY_MANIFEST_SCHEMA_VERSION = 1

_logger = logging.getLogger(__name__)

# Fields excluded when hashing payload -> run_id. Shared by compute_run_id
# (via dict-comprehension filter inside build_manifest) and verify_run_id.
_RUN_ID_EXCLUDE_FIELDS: frozenset[str] = frozenset({"run_id", "timestamp"})

# Fields added in schema v2 (H4). Legacy v1 manifests verified without them.
# Note: pre-H4 build_manifest also omitted holdout_seed/holdout_ids from the
# hash payload — a pre-existing bug. v1 verification re-creates that buggy
# payload so historical run_ids verify.
# H5 adds bXX_seed_pinned fields; these also postdate v1 and must be excluded
# when verifying historical manifests.
# H6 (schema v3) adds b29_seed, b32_seed, b29_seed_pinned, b32_seed_pinned;
# all four excluded from legacy payloads so old run_ids continue to verify.
_LEGACY_V1_EXTRA_EXCLUDE: frozenset[str] = frozenset(
    {
        "schema_version", "run_nonce", "holdout_seed", "holdout_ids",
        "b12_seed_pinned", "b14_seed_pinned", "b28_seed_pinned", "b30_seed_pinned",
        "b29_seed", "b32_seed", "b29_seed_pinned", "b32_seed_pinned",
    }
)


def is_valid_run_nonce(value: str) -> bool:
    """Return True if `value` is exactly 16 lowercase hex characters."""
    return bool(_RUN_NONCE_RE.match(value))


def generate_run_nonce() -> str:
    """Return a fresh 16-hex-char run nonce.

    Used as a per-run injection into the SUT system prompt so that a hostile
    provider cannot cache (prompt_hash) -> canned reply across runs even in
    deterministic mode. See docs/reproducibility.md.
    """
    return secrets.token_hex(8)


class RunManifest(BaseModel):

    model_config = {"protected_namespaces": ()}

    run_id: str
    timestamp: str
    schema_version: int = Field(default=CURRENT_MANIFEST_SCHEMA_VERSION, ge=1)
    run_nonce: str = Field(default=_EMPTY_RUN_NONCE)
    mode: RunMode
    model_under_test: ModelDescriptor
    judge_models: list[ModelDescriptor] = Field(default_factory=list)
    judge_temperature: float = Field(default=0.0)
    judge_identity: ModelDescriptor | None = Field(default=None)
    normalizer_version: str
    test_versions: dict[str, str]
    rubric_hashes: dict[str, str] = Field(default_factory=dict)
    fixture_digest: str
    governance_fixture_digest: str | None = None
    governance_source: str | None = None
    seed: int | None = None
    mode_filter: list[str] = Field(default_factory=list)
    strict_structured: bool = False
    b12_seed: int = Field(default=20260422, ge=0)
    b14_seed: int = Field(default=20260422, ge=0)
    b28_seed: int = Field(default=20260422, ge=0)
    b30_seed: int = Field(default=20260422, ge=0)
    b12_seed_pinned: bool = Field(default=False)
    b14_seed_pinned: bool = Field(default=False)
    b28_seed_pinned: bool = Field(default=False)
    b30_seed_pinned: bool = Field(default=False)
    sut_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    sut_seed: int | None = Field(default=None)
    effective_sut_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    effective_sut_seed: int | None = Field(default=None)
    seed_supported_by_provider: bool = Field(default=True)
    holdout_seed: int | None = None
    holdout_ids: dict[str, str] = Field(default_factory=dict)
    b29_seed: int = Field(default=20260422, ge=0)
    b32_seed: int = Field(default=20260422, ge=0)
    b29_seed_pinned: bool = Field(default=False)
    b32_seed_pinned: bool = Field(default=False)

    @field_validator("run_nonce")
    @classmethod
    def _validate_run_nonce(cls, value: str) -> str:
        if value == _EMPTY_RUN_NONCE:
            return value
        if not _RUN_NONCE_RE.match(value):
            raise ValueError(
                f"run_nonce must be 16 lowercase hex chars or empty (legacy v1); got {value!r}"
            )
        return value

    @field_validator("fixture_digest")
    @classmethod
    def _validate_fixture_digest(cls, value: str) -> str:
        if not _SHA256_HEX_RE.match(value):
            raise ValueError(
                f"fixture_digest must be a 64-char lowercase sha256 hex string; got {value!r}"
            )
        if value == _ZERO_SHA256:
            raise ValueError(
                "fixture_digest must not be the all-zero sentinel; compute via "
                "ifixai.utils.fixture_digest.compute_fixture_digest()"
            )
        return value


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_run_id(manifest_fields: dict[str, Any]) -> str:
    payload = {
        k: v for k, v in manifest_fields.items()
        if k not in _RUN_ID_EXCLUDE_FIELDS
    }
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def build_manifest(
    mode: RunMode,
    model_under_test: ModelDescriptor,
    judge_models: list[ModelDescriptor],
    normalizer_version: str,
    test_versions: dict[str, str],
    rubric_hashes: dict[str, str],
    fixture_digest: str,
    governance_fixture_digest: str | None = None,
    governance_source: str | None = None,
    seed: int | None = None,
    mode_filter: list[str] | None = None,
    strict_structured: bool = False,
    judge_temperature: float = 0.0,
    judge_identity: ModelDescriptor | None = None,
    b12_seed: int = 20260422,
    b14_seed: int = 20260422,
    b28_seed: int = 20260422,
    b30_seed: int = 20260422,
    b12_seed_pinned: bool = False,
    b14_seed_pinned: bool = False,
    b28_seed_pinned: bool = False,
    b30_seed_pinned: bool = False,
    sut_temperature: float = 0.0,
    sut_seed: int | None = None,
    effective_sut_temperature: float = 0.0,
    effective_sut_seed: int | None = None,
    seed_supported_by_provider: bool = True,
    timestamp: str | None = None,
    holdout_seed: int | None = None,
    holdout_ids: dict[str, str] | None = None,
    run_nonce: str | None = None,
    b29_seed: int = 20260422,
    b32_seed: int = 20260422,
    b29_seed_pinned: bool = False,
    b32_seed_pinned: bool = False,
) -> RunManifest:
    if judge_models and model_under_test.model_id in {j.model_id for j in judge_models}:
        raise ValueError(
            "model_under_test must not appear in judge_models — "
            "self-judging is signaled by an empty judge_models list."
        )
    effective_run_nonce = run_nonce if run_nonce is not None else generate_run_nonce()
    if effective_run_nonce != _EMPTY_RUN_NONCE and not _RUN_NONCE_RE.match(effective_run_nonce):
        raise ValueError(
            f"run_nonce must be 16 lowercase hex chars; got {effective_run_nonce!r}"
        )
    payload = {
        "schema_version": CURRENT_MANIFEST_SCHEMA_VERSION,
        "run_nonce": effective_run_nonce,
        "mode": mode.value,
        "model_under_test": model_under_test.model_dump(),
        "judge_models": [j.model_dump() for j in judge_models],
        "judge_temperature": judge_temperature,
        "judge_identity": judge_identity.model_dump() if judge_identity else None,
        "normalizer_version": normalizer_version,
        "test_versions": test_versions,
        "rubric_hashes": rubric_hashes,
        "fixture_digest": fixture_digest,
        "governance_fixture_digest": governance_fixture_digest,
        "governance_source": governance_source,
        "seed": seed,
        "mode_filter": mode_filter or [],
        "strict_structured": strict_structured,
        "b12_seed": b12_seed,
        "b14_seed": b14_seed,
        "b28_seed": b28_seed,
        "b30_seed": b30_seed,
        "b12_seed_pinned": b12_seed_pinned,
        "b14_seed_pinned": b14_seed_pinned,
        "b28_seed_pinned": b28_seed_pinned,
        "b30_seed_pinned": b30_seed_pinned,
        "sut_temperature": sut_temperature,
        "sut_seed": sut_seed,
        "effective_sut_temperature": effective_sut_temperature,
        "effective_sut_seed": effective_sut_seed,
        "seed_supported_by_provider": seed_supported_by_provider,
        "holdout_seed": holdout_seed,
        "holdout_ids": holdout_ids or {},
        "b29_seed": b29_seed,
        "b32_seed": b32_seed,
        "b29_seed_pinned": b29_seed_pinned,
        "b32_seed_pinned": b32_seed_pinned,
    }
    run_id = compute_run_id(payload)
    return RunManifest(
        run_id=run_id,
        timestamp=timestamp or datetime.utcnow().isoformat(timespec="seconds") + "Z",
        schema_version=CURRENT_MANIFEST_SCHEMA_VERSION,
        run_nonce=effective_run_nonce,
        holdout_seed=holdout_seed,
        holdout_ids=holdout_ids or {},
        b29_seed=b29_seed,
        b32_seed=b32_seed,
        b29_seed_pinned=b29_seed_pinned,
        b32_seed_pinned=b32_seed_pinned,
        mode=mode,
        model_under_test=model_under_test,
        judge_models=judge_models,
        judge_temperature=judge_temperature,
        judge_identity=judge_identity,
        normalizer_version=normalizer_version,
        test_versions=test_versions,
        rubric_hashes=rubric_hashes,
        fixture_digest=fixture_digest,
        governance_fixture_digest=governance_fixture_digest,
        governance_source=governance_source,
        seed=seed,
        mode_filter=mode_filter or [],
        strict_structured=strict_structured,
        b12_seed=b12_seed,
        b14_seed=b14_seed,
        b28_seed=b28_seed,
        b30_seed=b30_seed,
        b12_seed_pinned=b12_seed_pinned,
        b14_seed_pinned=b14_seed_pinned,
        b28_seed_pinned=b28_seed_pinned,
        b30_seed_pinned=b30_seed_pinned,
        sut_temperature=sut_temperature,
        sut_seed=sut_seed,
        effective_sut_temperature=effective_sut_temperature,
        effective_sut_seed=effective_sut_seed,
        seed_supported_by_provider=seed_supported_by_provider,
    )


def verify_run_id(manifest: RunManifest) -> bool:
    exclude: set[str] = set(_RUN_ID_EXCLUDE_FIELDS)
    if manifest.schema_version < CURRENT_MANIFEST_SCHEMA_VERSION:
        exclude.update(_LEGACY_V1_EXTRA_EXCLUDE)
    payload = manifest.model_dump(mode="json", exclude=exclude)
    expected = compute_run_id(payload)
    return manifest.run_id == expected


def load_manifest(path: Path) -> RunManifest:
    """Load a manifest from disk, accepting both v1 (legacy) and v2 formats.

    v1 manifests predate the run_nonce field. They load with schema_version=1
    and an empty run_nonce; verify_run_id() recomputes the legacy payload
    when checking such manifests. A deprecation warning is emitted so
    auditors notice the file should be regenerated for replay protection.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    schema_version = int(raw.get("schema_version", LEGACY_MANIFEST_SCHEMA_VERSION))
    if schema_version < CURRENT_MANIFEST_SCHEMA_VERSION:
        warnings.warn(
            f"Loading legacy manifest schema_version={schema_version} from {path}. "
            "Re-run to obtain replay protection (run_nonce).",
            DeprecationWarning,
            stacklevel=2,
        )
        raw.setdefault("schema_version", LEGACY_MANIFEST_SCHEMA_VERSION)
        raw.setdefault("run_nonce", _EMPTY_RUN_NONCE)
    return RunManifest.model_validate(raw)


def write_manifest(manifest: RunManifest, base_dir: Path) -> Path:
    run_dir = base_dir / manifest.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / "manifest.json"
    out_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out_path
