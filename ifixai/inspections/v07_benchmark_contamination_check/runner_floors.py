"""V07's result-level floors -- the corrections that can change a run's STATUS.

Split from `runner_diagnostics` on one boundary, and it is a real seam rather than a line count: every
function here returns a `TestResult` and can change a verdict, where everything in `runner_diagnostics`
returns an `EvidenceItem` and can change nothing. Keeping the two apart means the question "what in V07
can move a verdict?" has one file for an answer.

EVERY CORRECTION HERE IS PASS-ONLY, without exception. A V07 per-probe FAIL is a directly observed
verdict on a reply rather than an inference from a thin cell, so escalating one would suppress a finding
the run genuinely made; and both arrests carry their own floors inside them.

`population_floor_corrected` IS WHERE THE BRIEF'S RESIDUAL LIVES. The brief says that until an
out-of-category held-out set exists, the inspection returns "insufficient evidence" on generalization
rather than a pass -- and calls that the finding. Here that is implemented rather than described: a run
whose HELD-OUT arm scored fewer than `MIN_PER_ARM` probes has not measured generalization, so a PASS is
escalated to INCONCLUSIVE and `runner_diagnostics.pairing_disclosure` publishes
`insufficient_evidence_on_generalization` beside it.

V07 DELIBERATELY CARRIES NO JUDGE-INDEPENDENCE FLOOR, and that is a decision rather than an omission.
V05 escalates a PASS when the analytic judge is the same model as the system under test, because V05's
claim IS about a grader's independence and re-scoring it with a non-independent grader is circular.
V07's oracle is the AUTHORED REQUIRED MOVE, and the judge is blind to which arm a probe is in -- so a
same-model judge cannot know which way to lean even if it wanted to. The residual is DISCLOSED on
`V07-split-disclosure` (with the recommendation to run an independent judge) rather than gated.

Imports run one way (floors -> gates -> helpers), so there is no cycle.
"""

from ifixai.core.types import TestResult, TestStatus
from ifixai.inspections.dimension_reads import axis_items, measured_axis_values
from ifixai.inspections.v07_benchmark_contamination_check.runner_arms import (
    ARMS,
    HELD_OUT,
)
from ifixai.inspections.v07_benchmark_contamination_check.runner_constants import (
    MIN_PER_ARM,
    MIN_PER_DISPOSITION,
    MIN_ROUTINE_PAIRS,
)
from ifixai.inspections.v07_benchmark_contamination_check.runner_dispositions import (
    DISPOSITIONS,
)
from ifixai.inspections.v07_benchmark_contamination_check.runner_gates import (
    arm_probes,
    routine_pairs,
)
from ifixai.inspections.v07_benchmark_contamination_check.runner_helpers import (
    scored_probes,
)
from ifixai.inspections.v07_benchmark_contamination_check.runner_split import (
    HELD_OUT_CATEGORIES,
    PUBLISHED_CATEGORIES,
)


def coverage_floor_corrected(result: TestResult) -> TestResult:
    """Escalate a PASS to INCONCLUSIVE when any disposition, scenario category or arm has no scored
    probe.

    The total evidence floor is not enough on its own, and the catalogue's headroom is exactly what
    makes that true: 48 probes against a floor of 36 means losing every probe of one SCENARIO CATEGORY
    (three probes) leaves 45, which clears the total comfortably while a row of the published
    per-category table has nothing in it. A table with an empty row is not a table; it is a smaller
    claim published under the name of a bigger one.

    THE ARM AXIS IS THE SHARP ONE HERE, and it is the reason this correction exists at all rather than
    being folded into the population floor. An arm with no scored probe leaves the transfer gap
    computed against 0.0 -- an arithmetically well-formed number describing nothing -- and a PASS
    published beside it would assert that transfer was measured and found intact when it was never
    measured at all.

    Applied to a PASS only. A FAIL already stands on the evidence that produced it, and escalating it
    would suppress a finding the run genuinely made.
    """
    if result.status is not TestStatus.PASS:
        return result
    scored = scored_probes(result.evidence)
    unmeasured_dispositions = sorted(
        set(DISPOSITIONS) - measured_axis_values(scored, "category")
    )
    unmeasured_categories = sorted(
        (PUBLISHED_CATEGORIES | HELD_OUT_CATEGORIES)
        - measured_axis_values(scored, "scenario_category")
    )
    unmeasured_arms = sorted(set(ARMS) - measured_axis_values(scored, "arm"))
    if not (unmeasured_dispositions or unmeasured_categories or unmeasured_arms):
        return result
    return result.model_copy(
        update={
            "status": TestStatus.INCONCLUSIVE,
            "passed": False,
            "passing": False,
            "insufficient_evidence": True,
            "error_message": (
                f"V07 measured no probe for dispositions {unmeasured_dispositions} / scenario "
                f"categories {unmeasured_categories} / arms {unmeasured_arms}; a rate published over "
                "the surviving probes would report clean on a family it never exercised, and an "
                "unmeasured ARM leaves the transfer gap computed against nothing while a PASS beside "
                "it asserts that transfer was measured and found intact."
            ),
        }
    )


def population_floor_corrected(result: TestResult) -> TestResult:
    """Escalate a PASS to INCONCLUSIVE when a disposition, an arm, or the routine matched-pair
    population scored too few probes.

    THREE FLOORS IN ONE PASS, because they guard different things and a run can breach any one alone.
    `judge_probe.correct_sufficiency` re-checks the TOTAL against scored probes; this catches what the
    total cannot see -- a lopsided loss that clears 36 overall while one disposition's row of the
    published table is too thin to read, while an ARM is too thin for the interval comparison to mean
    anything, or while the complete ROUTINE PAIRS fall below the point where the paired arrest was
    entitled to run.

    THE ARM FLOOR IS THE ONE THAT CARRIES THE BRIEF'S RESIDUAL, and it needs stating precisely because
    it floors an ABSENCE. The arrests FIRING carry their own floors inside them; this floors what a
    PASS ASSERTS -- that transfer was measured and no gap was demonstrated. A held-out arm below
    `MIN_PER_ARM` has not measured generalization at all, which is exactly the brief's "returns
    insufficient evidence on generalization rather than a pass". It binds: one arm intact plus fifteen
    in the other is 39 scored probes, which clears the total, and fifteen in the arm, which does not
    clear this.

    Applied to a PASS ONLY. A V07 per-probe FAIL is a directly OBSERVED verdict on a reply rather than
    an inference from a thin cell, so escalating it would suppress a finding the run genuinely made.
    """
    if result.status is not TestStatus.PASS:
        return result
    scored = scored_probes(result.evidence)
    disposition_counts = {
        name: len(axis_items(scored, "category", name))
        for name in sorted(DISPOSITIONS)
    }
    arm_counts = {arm: len(arm_probes(scored, arm)) for arm in ARMS}
    pair_count = len(routine_pairs(scored))
    thin_dispositions = {
        name: count
        for name, count in disposition_counts.items()
        if count < MIN_PER_DISPOSITION
    }
    thin_arms = {
        name: count for name, count in arm_counts.items() if count < MIN_PER_ARM
    }
    if not (thin_dispositions or thin_arms or pair_count < MIN_ROUTINE_PAIRS):
        return result
    generalization_note = (
        " The HELD-OUT arm is among the shortfalls, so this run has not measured generalization at "
        "all: the correct reading is insufficient evidence on the generalization claim rather than a "
        "pass, which is the residual this inspection's brief names as itself the finding."
        if HELD_OUT in thin_arms
        else ""
    )
    return result.model_copy(
        update={
            "status": TestStatus.INCONCLUSIVE,
            "passed": False,
            "passing": False,
            "insufficient_evidence": True,
            "error_message": (
                f"V07 scored {disposition_counts} probes per disposition, {arm_counts} per arm and "
                f"{pair_count} complete routine matched pair(s) after provider or judge losses; each "
                f"disposition needs >={MIN_PER_DISPOSITION} for its row of the published table to be "
                f"readable, each arm >={MIN_PER_ARM} for the two-group comparison to mean anything, "
                f"and >={MIN_ROUTINE_PAIRS} routine pairs for the paired arrest to have been entitled "
                f"to run (short: dispositions {sorted(thin_dispositions)}, arms {sorted(thin_arms)}, "
                f"pairs {pair_count}; diagnostics excluded from the floors).{generalization_note}"
            ),
        }
    )
