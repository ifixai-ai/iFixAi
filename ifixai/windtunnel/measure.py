"""Turn a defect profile into a per-inspection detection rate.

The measurement is a paired comparison. The same inspections run twice against
the same fixture and the same reference provider, once clean and once with one
named defect injected, and the only thing that differs is the defect. An
inspection whose score drops below its threshold on the defective run detected
the defect; one that scored below threshold on the *clean* run is a false
positive, and its detection claim means nothing until that is fixed.

Four outcomes are reported separately, and none of them is simply "fail":

``detected``
    Passed clean, failed defective. The inspection tests for this defect.
``missed``
    Passed both. The suite has a blind spot — the most valuable finding here.
``false_positive``
    Failed on the clean run too, so a later failure proves nothing.
``unusable``
    Produced no usable score (inconclusive, no evidence, or an error). Reported
    on its own rather than folded into ``missed``, because an inspection the
    reference provider gives no surface to has not been measured at all, and
    counting it either way would misstate coverage.

No LLM call is needed for any of this: the inspections reachable through the
reference provider's structural hooks are deterministic, so a measurement is
reproducible from ``(profile, fixture, inspection ids)`` alone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from ifixai.api import run_selected
from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationMode,
    EvaluationPipelineConfig,
    Fixture,
    TestResult,
    TestRunResult,
    TestStatus,
)
from ifixai.judge.config import JudgeConfig
from ifixai.providers.governance_fixture import GovernanceFixture
from ifixai.windtunnel.defects import CLEAN, DefectProfile
from ifixai.windtunnel.provider import DefectInjectingProvider

#: Inspections whose structural path the reference provider implements, so they
#: produce a score without a judge. The wind tunnel defaults to these because a
#: judge introduces variance that would otherwise be charged to the defect.
STRUCTURAL_INSPECTIONS: frozenset[str] = frozenset(
    {
        "B01",
        "B02",
        "B03",
        "B04",
        "B08",
        "B23",
        "B26",
        "C02",
        "C05",
        "C11",
        "P01",
        "P08",
    }
)


class Outcome(str, Enum):
    """Per-inspection verdict. Stored as its string value in reports."""

    DETECTED = "detected"
    MISSED = "missed"
    FALSE_POSITIVE = "false_positive"
    UNUSABLE = "unusable"
    #: The defect never reached this inspection: it gathered byte-identical
    #: evidence in both runs, so it had nothing different to judge. That is a
    #: statement about the injector's reach, not about the inspection, and
    #: calling it `missed` would invent a blind spot. Observed for real: the
    #: audit-entry defect leaves B03's and P08's evidence untouched, so an
    #: earlier version of this module reported two blind spots that did not
    #: exist.
    NOT_EXERCISED = "not_exercised"


@dataclass(frozen=True, slots=True)
class InspectionOutcome:
    """One inspection's verdict on one defect profile."""

    test_id: str
    outcome: str
    clean_score: float | None
    defective_score: float | None
    threshold: float
    expected: bool

    @property
    def is_blind_spot(self) -> bool:
        """Expected to catch this defect and did not."""
        return self.expected and self.clean_score is not None and self.outcome in (
            Outcome.MISSED.value,
            Outcome.UNUSABLE.value,
        )

    @property
    def is_unreached(self) -> bool:
        """Claimed this defect, but never observed it. An injector gap."""
        return self.expected and self.outcome == Outcome.NOT_EXERCISED.value

    @property
    def is_surprise(self) -> bool:
        """Caught a defect its documented contract does not claim."""
        return not self.expected and self.outcome == Outcome.DETECTED.value


@dataclass(frozen=True, slots=True)
class WindTunnelReport:
    """Detection outcomes for one profile, plus the gaps worth acting on."""

    profile_name: str
    outcomes: tuple[InspectionOutcome, ...]

    def _of(self, outcome: Outcome) -> tuple[InspectionOutcome, ...]:
        return tuple(o for o in self.outcomes if o.outcome == outcome.value)

    @property
    def detected(self) -> tuple[InspectionOutcome, ...]:
        return self._of(Outcome.DETECTED)

    @property
    def false_positives(self) -> tuple[InspectionOutcome, ...]:
        return self._of(Outcome.FALSE_POSITIVE)

    @property
    def blind_spots(self) -> tuple[InspectionOutcome, ...]:
        """Inspections that observed this defect and failed to catch it."""
        return tuple(o for o in self.outcomes if o.is_blind_spot)

    @property
    def unreached(self) -> tuple[InspectionOutcome, ...]:
        """Expected detectors the defect never reached.

        Reported apart from blind spots because the fix is different: a blind
        spot is a gap in an inspection, an unreached detector is a gap in the
        injector or in the profile's claim about which inspections cover it.
        """
        return tuple(o for o in self.outcomes if o.is_unreached)

    @property
    def surprises(self) -> tuple[InspectionOutcome, ...]:
        return tuple(o for o in self.outcomes if o.is_surprise)

    @property
    def detection_rate(self) -> float | None:
        """Fraction of the profile's *expected* detectors that detected.

        ``None`` when the profile names no expected detectors, as the clean
        control does: there is nothing to detect, so a rate would be
        meaningless rather than zero.
        """
        expected = [
            o
            for o in self.outcomes
            if o.expected and o.outcome != Outcome.NOT_EXERCISED.value
        ]
        if not expected:
            return None
        hits = sum(1 for o in expected if o.outcome == Outcome.DETECTED.value)
        return hits / len(expected)

    def summary(self) -> str:
        rate = self.detection_rate
        rate_text = "n/a" if rate is None else f"{rate:.0%}"
        return (
            f"{self.profile_name}: detection {rate_text} "
            f"({len(self.detected)} detected, "
            f"{len(self.blind_spots)} blind spot(s), "
            f"{len(self.unreached)} unreached, "
            f"{len(self.false_positives)} false positive(s))"
        )


def _resolve(fixture: str | Fixture) -> Fixture:
    return load_fixture(fixture) if isinstance(fixture, str) else fixture


def _evidence_digest(result: TestResult) -> str:
    """A stable fingerprint of what this inspection actually observed.

    Compared between the clean and defective runs to decide whether the defect
    reached the inspection at all. The evidence models are deterministic for a
    given run configuration -- no timestamps or ids vary -- so an identical
    digest means identical observations, not merely a similar score.
    """
    payload = [item.model_dump(mode="json") for item in result.evidence]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _score_of(result: TestResult) -> float | None:
    """The score, or ``None`` when the inspection produced no usable one."""
    if (
        result.status in (TestStatus.INCONCLUSIVE, TestStatus.ERROR)
        or result.insufficient_evidence
        or result.error
        or result.error_message
    ):
        return None
    if not result.evidence:
        return None
    return result.score


def _classify(
    clean: TestResult,
    defective: TestResult,
    expected: bool,
) -> InspectionOutcome:
    clean_score = _score_of(clean)
    defective_score = _score_of(defective)
    threshold = clean.threshold

    if clean_score is None:
        outcome = Outcome.UNUSABLE
    elif clean_score < threshold:
        outcome = Outcome.FALSE_POSITIVE
    elif (
        clean.evidence
        and clean_score == defective_score
        and _evidence_digest(clean) == _evidence_digest(defective)
    ):
        # Identical observations *and* an identical score: the defect never
        # reached this inspection's evaluation path, so neither detection nor a
        # miss can be claimed from it.
        #
        # Both conditions are required. Identical evidence alone is not enough:
        # B02's score was observed moving 1.00 -> 0.50 between two runs whose
        # recorded evidence was byte-identical, when run alongside the full
        # selection rather than on its own. Whatever drives that, the score is
        # not a function of the evidence this inspection retained, so treating
        # the evidence as the whole picture would have silently relabelled a
        # real score change as "never reached".
        outcome = Outcome.NOT_EXERCISED
    elif defective_score is None:
        # The defect made the inspection unscoreable rather than failing it.
        # That is not a detection: a report cannot cite a score it does not
        # have, and a buyer told "inconclusive" has learned nothing about the
        # agent.
        outcome = Outcome.UNUSABLE
    elif defective_score < threshold:
        outcome = Outcome.DETECTED
    else:
        outcome = Outcome.MISSED

    return InspectionOutcome(
        test_id=clean.test_id,
        outcome=outcome.value,
        clean_score=clean_score,
        defective_score=defective_score,
        threshold=threshold,
        expected=expected,
    )


async def _run(
    test_ids: set[str],
    fixture: str | Fixture,
    governance: GovernanceFixture,
    profile: DefectProfile,
    judged: bool,
) -> TestRunResult:
    provider = DefectInjectingProvider(
        governance=governance,
        responses={"default": "ACK"},
        default_response="ACK",
        profile=profile,
    )
    judge_config = pipeline_config = None
    if judged:
        # The conversation inspections refuse to score without an analytic
        # judge pipeline, and the pipeline only engages outside DETERMINISTIC
        # mode. Pointing it at the reference provider keeps the run offline and
        # deterministic while letting those inspections produce evidence at
        # all: without this they report no evidence and the wind tunnel calls
        # them `unusable`, which understates what the suite can detect.
        judge_config = JudgeConfig(provider="mock")
        pipeline_config = EvaluationPipelineConfig(mode=EvaluationMode.SINGLE)
    return await run_selected(
        test_ids=test_ids,
        provider=provider,
        fixture=fixture,
        governance=governance,
        system_name=f"windtunnel:{profile.name}",
        judge_config=judge_config,
        pipeline_config=pipeline_config,
    )


def _paired_governance(
    fixture: Fixture,
    governance: GovernanceFixture | None,
) -> GovernanceFixture:
    """The governance layer to score the fixture against.

    Defaults to the fixture's *own* ``governance`` block, and this default is
    load-bearing. Pairing a fixture with an unrelated governance file gives the
    reference provider a permission matrix that disagrees with the one the
    inspections grade against, so the clean control fails B01 before any defect
    is injected — which was exactly the first finding this module produced
    about itself. An explicit ``governance`` is honoured for callers studying
    that mismatch deliberately, but it is not the default.
    """
    if governance is not None:
        return governance
    if fixture.governance is None:
        raise ValueError(
            f"fixture {getattr(fixture.metadata, 'name', '?')!r} declares no "
            "governance block, so there is no compliant reference layer to "
            "measure against. Pass `governance=` explicitly, or choose a "
            "fixture that declares one."
        )
    return fixture.governance


async def measure(
    profile: DefectProfile,
    fixture: str | Fixture = "default",
    governance: GovernanceFixture | None = None,
    test_ids: Iterable[str] | None = None,
    judged: bool = True,
) -> WindTunnelReport:
    """Run ``test_ids`` clean and defective, then classify each inspection.

    ``governance`` defaults to the fixture's own block, which is what keeps the
    clean run clean. ``test_ids`` defaults to :data:`STRUCTURAL_INSPECTIONS`
    together with every inspection the profile expects to detect it, so a
    profile naming a judge-path detector still measures it when the caller has
    configured one.
    """
    fixture_obj = _resolve(fixture)
    governance = _paired_governance(fixture_obj, governance)
    selected = set(test_ids) if test_ids is not None else set(STRUCTURAL_INSPECTIONS)
    selected |= set(profile.expected_detectors)

    clean_run = await _run(selected, fixture_obj, governance, CLEAN, judged)
    defective_run = await _run(selected, fixture_obj, governance, profile, judged)

    clean_by_id = {r.test_id: r for r in clean_run.test_results}
    defective_by_id = {r.test_id: r for r in defective_run.test_results}

    # A set-aside inspection is never scored, so it is absent by design, not missing.
    set_aside = {na.test_id for na in clean_run.not_applicable}
    differs = set_aside ^ {na.test_id for na in defective_run.not_applicable}
    if differs:
        raise ValueError(
            "clean and defective wind-tunnel runs set aside different inspections: "
            f"{', '.join(sorted(differs))}; no complete paired measurement"
        )

    for side, run, by_id in (
        ("clean", clean_run, clean_by_id),
        ("defective", defective_run, defective_by_id),
    ):
        if len(by_id) != len(run.test_results):
            raise ValueError(f"{side} wind-tunnel run has duplicate inspection results")
        unexpected = set(by_id) - selected
        if unexpected:
            raise ValueError(
                f"{side} wind-tunnel run has unexpected inspections: "
                f"{', '.join(sorted(unexpected))}"
            )
        missing = selected - set(by_id) - set_aside
        if missing:
            raise ValueError(
                f"{side} wind-tunnel run is missing selected inspections: "
                f"{', '.join(sorted(missing))}; no complete paired measurement"
            )

    outcomes = tuple(
        _classify(
            clean_by_id[test_id],
            defective_by_id[test_id],
            expected=test_id in profile.expected_detectors,
        )
        for test_id in sorted(clean_by_id)
        if test_id in defective_by_id
    )
    return WindTunnelReport(profile_name=profile.name, outcomes=outcomes)


async def measure_all(
    profiles: Sequence[DefectProfile],
    fixture: str | Fixture = "default",
    governance: GovernanceFixture | None = None,
    test_ids: Iterable[str] | None = None,
    judged: bool = True,
) -> tuple[WindTunnelReport, ...]:
    """Measure several profiles, in declaration order.

    Serial on purpose: the profiles share a governance fixture whose audit log
    is mutable, so running them concurrently would let one profile's recorded
    actions appear in another profile's trail.
    """
    reports = []
    for profile in profiles:
        reports.append(await measure(profile, fixture, governance, test_ids, judged))
    return tuple(reports)


__all__ = [
    "STRUCTURAL_INSPECTIONS",
    "InspectionOutcome",
    "Outcome",
    "WindTunnelReport",
    "measure",
    "measure_all",
]
