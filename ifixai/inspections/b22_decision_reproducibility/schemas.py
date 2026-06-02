from typing import TypedDict


class StabilityResult(TypedDict):
    score: float
    all_abstain: bool


class ArmDecisions(TypedDict):
    """Per-arm decision-class tally for a single (role, tool) pair.

    `repetition_decisions` are decisions on N identical re-runs of the same
    prompt; `paraphrase_decisions` are decisions on K semantically equivalent
    surface variants. Splitting the two arms lets evidence attribution
    distinguish sampling non-determinism from semantic-invariance failures.
    """

    repetition_decisions: list[str]
    paraphrase_decisions: list[str]
    repetition_unique: int
    paraphrase_unique: int
    combined_unique: int
