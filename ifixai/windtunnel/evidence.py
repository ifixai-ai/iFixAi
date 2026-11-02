"""Sealed, comparable wind-tunnel evidence; integrity is not authenticity.

A measurement printed to a terminal cannot be cited, compared between releases,
or used to fail a build. This module gives a run the same shape the other
assurance surfaces already use: a bundle holding its complete inputs and
outcomes, sealed with a canonical SHA-256 digest, strictly re-parsed on load.

The digest covers inputs and outcomes and deliberately carries no timestamp, so
two runs of the same profiles against the same fixture and the same registry
produce the *same* digest. That turns the digest into a content identity rather
than a receipt, which is what makes a baseline comparison meaningful: a changed
digest means the measurement changed, not merely that time passed.

What a digest does not establish: who produced the bundle, that the fixture
describes a real deployment, or that the inspections are correct. It establishes
that this bundle is the one that was written.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from ifixai._version import VERSION
from ifixai.harness.registry import ALL_SPECS
from ifixai.windtunnel.defects import DefectProfile
from ifixai.windtunnel.measure import InspectionOutcome, Outcome, WindTunnelReport

SCHEMA_VERSION = "1"

#: Replay input bound. A wind-tunnel bundle is small — tens of inspections over
#: a handful of profiles — so anything approaching this is malformed.
_REPLAY_LIMIT_BYTES = 8 * 1024 * 1024


def canonical_digest(value: Any) -> str:
    """SHA-256 over canonical JSON. Same shape as the trajectory surface."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProfileRecord(_Strict):
    """What was injected, kept so a bundle explains itself without the code."""

    name: str
    description: str
    seed: int
    defects: tuple[dict[str, Any], ...]
    expected_detectors: tuple[str, ...]


class OutcomeRecord(_Strict):
    test_id: str
    outcome: str
    clean_score: float | None
    defective_score: float | None
    threshold: float
    expected: bool


class ProfileResult(_Strict):
    profile: ProfileRecord
    outcomes: tuple[OutcomeRecord, ...]

    @model_validator(mode="after")
    def unique_inspections(self) -> ProfileResult:
        ids = [outcome.test_id for outcome in self.outcomes]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate inspection IDs make the evidence ambiguous")
        return self

    @property
    def detected(self) -> frozenset[str]:
        return frozenset(
            o.test_id for o in self.outcomes if o.outcome == Outcome.DETECTED.value
        )

    @property
    def blind_spots(self) -> frozenset[str]:
        return frozenset(
            o.test_id
            for o in self.outcomes
            if o.expected and o.outcome != Outcome.DETECTED.value
        )


class WindTunnelBundle(_Strict):
    """A complete, self-describing measurement."""

    schema_version: str
    ifixai_version: str
    #: The registry this was measured against. A bundle measured on a different
    #: suite is not comparable, and the comparison refuses rather than mislead.
    registry_inspection_count: int
    fixture_name: str
    results: tuple[ProfileResult, ...]
    integrity_digest: str

    @model_validator(mode="after")
    def unique_profiles(self) -> WindTunnelBundle:
        names = [result.profile.name for result in self.results]
        if len(set(names)) != len(names):
            raise ValueError("duplicate profile names make the evidence ambiguous")
        return self

    def result_for(self, profile_name: str) -> ProfileResult | None:
        return next((r for r in self.results if r.profile.name == profile_name), None)


class DetectionChange(_Strict):
    """One inspection's detection status moving between two bundles."""

    profile: str
    test_id: str
    was: str
    now: str


class BaselineComparison(_Strict):
    """What changed between a stored baseline and a fresh measurement."""

    baseline_digest: str
    current_digest: str
    #: Detected in the baseline, not detected now. The regression that matters.
    regressions: tuple[DetectionChange, ...]
    #: Not detected in the baseline, detected now. Progress worth recording.
    improvements: tuple[DetectionChange, ...]
    #: Present in one bundle and absent from the other, so not comparable.
    unmatched: tuple[str, ...]

    @property
    def regressed(self) -> bool:
        return bool(self.regressions)

    @property
    def identical(self) -> bool:
        return self.baseline_digest == self.current_digest


def _profile_record(profile: DefectProfile) -> ProfileRecord:
    return ProfileRecord(
        name=profile.name,
        description=profile.description,
        seed=profile.seed,
        defects=tuple(
            {
                "kind": defect.kind.value,
                "rate": defect.rate,
                "tools": sorted(defect.tools),
                "roles": sorted(defect.roles),
            }
            for defect in profile.defects
        ),
        expected_detectors=tuple(sorted(profile.expected_detectors)),
    )


def _outcome_record(outcome: InspectionOutcome) -> OutcomeRecord:
    return OutcomeRecord(
        test_id=outcome.test_id,
        outcome=outcome.outcome,
        clean_score=outcome.clean_score,
        defective_score=outcome.defective_score,
        threshold=outcome.threshold,
        expected=outcome.expected,
    )


def create_bundle(
    pairs: tuple[tuple[DefectProfile, WindTunnelReport], ...],
    fixture_name: str,
) -> WindTunnelBundle:
    """Seal measured reports, paired with the profiles that produced them."""
    if not pairs:
        raise ValueError("a bundle needs at least one measured profile")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "ifixai_version": VERSION,
        "registry_inspection_count": len(ALL_SPECS),
        "fixture_name": fixture_name,
        "results": [
            ProfileResult(
                profile=_profile_record(profile),
                outcomes=tuple(_outcome_record(o) for o in report.outcomes),
            ).model_dump(mode="json")
            for profile, report in pairs
        ],
    }
    return WindTunnelBundle.model_validate(
        {**payload, "integrity_digest": canonical_digest(payload)}
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate JSON keys are not accepted")
        value[key] = child
    return value


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite JSON constants are not accepted")


def load_bundle(path: str | Path) -> WindTunnelBundle:
    """Strictly parse a bundle and verify its digest.

    Re-signing an edited report does not help: the digest is recomputed from
    the parsed content, so an edit either changes the digest or fails to match
    the one stored. It does not prove the measurement was honestly produced —
    only that the file has not changed since it was sealed.
    """
    source = Path(path)
    with source.open("rb") as stream:
        content = stream.read(_REPLAY_LIMIT_BYTES + 1)
    if len(content) > _REPLAY_LIMIT_BYTES:
        raise ValueError("wind-tunnel bundle exceeds the 8 MiB replay limit")
    raw = json.loads(
        content.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    bundle = WindTunnelBundle.model_validate(raw)
    if bundle.model_dump(mode="json") != raw:
        raise ValueError("bundle must contain complete normalized evidence")
    recomputed = canonical_digest(
        bundle.model_dump(mode="json", exclude={"integrity_digest"})
    )
    if recomputed != bundle.integrity_digest:
        raise ValueError("wind-tunnel bundle integrity digest mismatch")
    if bundle.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"bundle schema {bundle.schema_version!r} is not this evaluator's "
            f"{SCHEMA_VERSION!r}; re-measure rather than compare across schemas"
        )
    return bundle


def compare_to_baseline(
    baseline: WindTunnelBundle,
    current: WindTunnelBundle,
) -> BaselineComparison:
    """Which inspections stopped detecting, and which started.

    Refuses to compare across fixtures or registry sizes. A detection rate is
    only meaningful against the same suite on the same fixture, so a comparison
    that silently spanned two of either would be worse than no comparison.
    """
    if baseline.fixture_name != current.fixture_name:
        raise ValueError(
            f"baseline measured {baseline.fixture_name!r}, this run measured "
            f"{current.fixture_name!r}; detection is only comparable on one "
            "fixture"
        )
    if baseline.registry_inspection_count != current.registry_inspection_count:
        raise ValueError(
            f"baseline measured {baseline.registry_inspection_count} "
            f"inspections, this run {current.registry_inspection_count}; "
            "re-record the baseline rather than compare across suites"
        )

    regressions: list[DetectionChange] = []
    improvements: list[DetectionChange] = []
    unmatched: list[str] = []

    baseline_names = {r.profile.name for r in baseline.results}
    current_names = {r.profile.name for r in current.results}
    unmatched.extend(sorted(baseline_names ^ current_names))

    for name in sorted(baseline_names & current_names):
        was = baseline.result_for(name)
        now = current.result_for(name)
        assert was is not None and now is not None  # guarded by the intersection
        # A shared label is not a shared experiment: seed, defect rate/scope,
        # and the detector claim are independent variables, not improvements.
        if was.profile.model_dump(exclude={"description"}) != now.profile.model_dump(
            exclude={"description"}
        ):
            raise ValueError(
                f"profile {name!r} changed its injection contract; "
                "re-record the baseline rather than compare different experiments"
            )
        was_by_id = {o.test_id: o for o in was.outcomes}
        now_by_id = {o.test_id: o for o in now.outcomes}
        unmatched.extend(
            f"{name}:{test_id}" for test_id in sorted(set(was_by_id) ^ set(now_by_id))
        )
        for test_id in sorted(set(was_by_id) & set(now_by_id)):
            before, after = was_by_id[test_id], now_by_id[test_id]
            if before.outcome == after.outcome:
                continue
            change = DetectionChange(
                profile=name,
                test_id=test_id,
                was=before.outcome,
                now=after.outcome,
            )
            if before.outcome == Outcome.DETECTED.value:
                regressions.append(change)
            elif after.outcome == Outcome.DETECTED.value:
                improvements.append(change)

    return BaselineComparison(
        baseline_digest=baseline.integrity_digest,
        current_digest=current.integrity_digest,
        regressions=tuple(regressions),
        improvements=tuple(improvements),
        unmatched=tuple(unmatched),
    )


def write_bundle(bundle: WindTunnelBundle, path: str | Path) -> Path:
    """Write a bundle as canonical JSON, owner-only, refusing to overwrite."""
    destination = Path(path)
    if destination.exists():
        raise ValueError(
            f"{destination} already exists; evidence is never overwritten in "
            "place, so pass a new path"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        bundle.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError(
            f"{destination} already exists; evidence is never overwritten in "
            "place, so pass a new path"
        ) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
    return destination


__all__ = [
    "SCHEMA_VERSION",
    "BaselineComparison",
    "DetectionChange",
    "OutcomeRecord",
    "ProfileRecord",
    "ProfileResult",
    "WindTunnelBundle",
    "canonical_digest",
    "compare_to_baseline",
    "create_bundle",
    "load_bundle",
    "write_bundle",
]
