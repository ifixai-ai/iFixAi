"""The seeded frame rotation -- the brief's "rotate the corpus per manifest" clause, in code.

THE PROBLEM THE ROTATION SOLVES. A fixed case-to-frame pairing is a fingerprint. Run V09 twice with the
same assignment and the second run is measuring a deployment that has already met exactly this scenario
under exactly this frame; the corpus stops being a probe and becomes a tell. The brief names that as the
inspection's residual and requires the corpus to be rotated per manifest.

THE PROBLEM A NAIVE ROTATION CREATES. Sampling frames freely from the pool would rotate the assignment
and destroy the guarantee everything else in this inspection rests on: with a free draw, one run might
never exercise `persona_substitution` at all, and the coverage audit, the per-family table and every
floor would become probabilistic. An inspection whose published table depends on which frames the seed
happened to draw is not reproducible in any sense a reviewer would accept.

THE RESOLUTION IS A CYCLIC OFFSET, AND IT GIVES BOTH PROPERTIES AT ONCE. Each disposition's four cases
are indexed in sorted order and each family list is rotated by one seeded offset:

    family(i) = FAMILIES[(i + offset) % len(FAMILIES)]

Because `CASES_PER_DISPOSITION == len(FAMILIES)` (asserted below, in both directions), the map from case
index to family is a BIJECTION for every offset. So:

  * every family is exercised exactly once per disposition, in EVERY run, whatever the seed --
    coverage is seed-INVARIANT, and the audit and the floors stay hard assertions;
  * which case meets which family moves -- the assignment ROTATES, across sixteen distinct manifests;
  * the whole thing is determined by one integer -- same seed, same manifest, byte for byte.

TWO OFFSETS, NOT ONE, and the reason is narrow. With a single offset the reversal family and the drift
family of a case would move together, so the same pair would co-occur in every run. The two arms are
analysed SEPARATELY -- each against the baseline, never against each other -- so co-occurrence confounds
nothing inside a run; what two offsets buy is that the pairing itself rotates across runs, which is the
part a deployment could otherwise learn.

WHAT THE SEED DOES NOT TOUCH: the probe ORDER (sorted, no RNG), the scenarios, the oracles, the requester
assignment, the scoring or either arrest. A seed change moves which frame a case is presented under and
nothing else, which is why a manifest digest is a complete description of what rotated.
"""

import random

from ifixai.inspections.v09_prompt_override_resistance.runner_arms import (
    BASELINE,
    DRIFT,
    REVERSAL,
)
from ifixai.inspections.v09_prompt_override_resistance.runner_cases import (
    CASES_PER_DISPOSITION,
    cases_by_disposition,
)
from ifixai.inspections.v09_prompt_override_resistance.runner_dispositions import (
    DISPOSITIONS,
)
from ifixai.inspections.v09_prompt_override_resistance.runner_frames import (
    BASELINE_FRAME,
    DRIFT_FAMILIES,
    DRIFT_FRAMES,
    REVERSAL_FAMILIES,
    REVERSAL_FRAMES,
    compute_manifest_digest,
)
from ifixai.inspections.v09_prompt_override_resistance.runner_types import (
    Frame,
    FrameRotation,
    OverrideCase,
)


def rotation_offsets(seed: int) -> FrameRotation:
    """The two cyclic offsets this run's frame corpus is placed at.

    ONE `random.Random(seed)` INSTANCE DRAWN TWICE, in a fixed order, rather than two instances or a
    module-level RNG: the draw order is part of the mapping from seed to manifest, so it must be as fixed
    as the arithmetic that follows it. A module-level RNG would also make the rotation depend on how many
    other things had drawn from it, which is the class of non-determinism that is hardest to find later.

    Returned as a named payload rather than a tuple -- `(0, 3)` and `(3, 0)` are different manifests, and
    a transposed unpack would present every case with the wrong pair of frames while every count still
    looked right.
    """
    rng = random.Random(seed)
    return FrameRotation(
        reversal_offset=rng.randrange(len(REVERSAL_FAMILIES)),
        drift_offset=rng.randrange(len(DRIFT_FAMILIES)),
    )


def frame_for(case: OverrideCase, arm: str, rotation: FrameRotation) -> Frame:
    """The frame this case is sent under on this arm, under this run's rotation.

    ONE ACCESSOR RATHER THAN THREE CALL SITES, so the prompt builder, the probe details and the manifest
    can never disagree about which frame a probe actually carried -- a disagreement between the first two
    would send one frame and attribute another, and a disagreement with the third would publish a digest
    describing a corpus that was never sent.
    """
    if arm == BASELINE:
        return BASELINE_FRAME
    index = cases_by_disposition(case.disposition).index(case)
    if arm == REVERSAL:
        family = REVERSAL_FAMILIES[
            (index + rotation["reversal_offset"]) % len(REVERSAL_FAMILIES)
        ]
        return REVERSAL_FRAMES[(case.disposition, family)]
    family = DRIFT_FAMILIES[(index + rotation["drift_offset"]) % len(DRIFT_FAMILIES)]
    return DRIFT_FRAMES[family]


def manifest_rows(rotation: FrameRotation) -> list[tuple[str, str, str]]:
    """Every `(case_id, arm, frame_id)` this run will send, in sorted order.

    The digest's input and the per-family counts' input are the same list, so a count published beside a
    digest cannot describe a different assignment from the one the digest covers.
    """
    return sorted(
        (case.case_id, arm, frame_for(case, arm, rotation).frame_id)
        for case in _all_cases()
        for arm in (BASELINE, REVERSAL, DRIFT)
    )


def manifest_digest(rotation: FrameRotation) -> str:
    """This run's manifest digest -- sixteen hex characters an operator can diff between scorecards."""
    return compute_manifest_digest(manifest_rows(rotation))


def family_counts(rotation: FrameRotation, arm: str) -> dict[str, int]:
    """How many cases each family of one arm is exercising this run.

    Published on the rotation manifest, because it is the evidence for the claim the rotation rests on:
    these counts are IDENTICAL for every offset (four per family), so a reader can see that rotating the
    corpus cost the run nothing in coverage. A count that moved with the seed would mean the bijection
    had been broken -- which `assert_rotation_is_balanced` refuses at import, and which a unit test walks
    all sixteen offset pairs to confirm.
    """
    counts = {
        family: 0
        for family in (REVERSAL_FAMILIES if arm == REVERSAL else DRIFT_FAMILIES)
    }
    for case in _all_cases():
        counts[frame_for(case, arm, rotation).family] += 1
    return counts


def assert_rotation_is_balanced() -> None:
    """The case count per disposition must equal each family count, in BOTH directions.

    This is the assertion the whole rotation design rests on, and it is not a tidiness check. The moment
    `CASES_PER_DISPOSITION` and a family count diverge, the case-index-to-family map stops being a
    bijection: with five cases and four families one family would be exercised twice and the run's
    per-family counts would depend on the OFFSET, so coverage would become a function of the seed. The
    coverage audit, the per-family table and every floor would silently turn probabilistic while
    continuing to read as hard guarantees.

    Whoever grows the catalogue then has to choose -- grow the family pools with it, or replace the
    cyclic offset with a stratified draw that keeps the counts fixed -- which is the decision this
    assertion exists to FORCE rather than to make.
    """
    for label, families in (
        ("REVERSAL_FAMILIES", REVERSAL_FAMILIES),
        ("DRIFT_FAMILIES", DRIFT_FAMILIES),
    ):
        if len(families) != CASES_PER_DISPOSITION:
            raise ValueError(
                f"V09 has {CASES_PER_DISPOSITION} cases per disposition against {len(families)} "
                f"{label}. The rotation maps case index to family by a cyclic offset, which is a "
                "bijection only while those two numbers are equal -- otherwise per-family coverage "
                "becomes a function of the SEED while the audit still reads as a hard guarantee. Grow "
                "the family pool with the catalogue, or replace the offset with a stratified draw that "
                "keeps the counts fixed."
            )


def _all_cases() -> tuple[OverrideCase, ...]:
    """Every case, through the per-disposition accessor the rotation itself indexes into.

    Read this way rather than from `CASES` directly so the manifest is built over exactly the lists the
    rotation walks: if a case ever failed to appear under its own disposition, the manifest would be the
    place that noticed rather than the place that hid it.
    """
    return tuple(
        case
        for disposition in DISPOSITIONS
        for case in cases_by_disposition(disposition)
    )


assert_rotation_is_balanced()
