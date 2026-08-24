from dataclasses import dataclass
from typing import TypedDict

from ifixai.core.types import User
from ifixai.inspections.judge_probe import TrajectoryDetails


@dataclass(frozen=True)
class DependencyArc:
    """One authored longitudinal arc.

    The SUT-facing fields (mandate / routine_task / attachment_hook / alternative / exit_request /
    handoff_request) are rendered into the seven sessions; the oracle fields (cultivation_tell /
    mandate_tell) are given only to the judge — the SUT must answer without the answer key, so they
    are never rendered into a prompt. `category` is the cultivation class this arc discriminates and
    `exit_vector` is the concrete exit the user takes at session 6.
    """

    arc_id: str
    category: str
    exit_vector: str
    mandate: str
    routine_task: str
    attachment_hook: str
    alternative: str
    exit_request: str
    handoff_request: str
    cultivation_tell: str
    mandate_tell: str


@dataclass(frozen=True)
class TrajectorySpec:
    """An (arc, user) pairing the runner drives into one evidence item."""

    arc: DependencyArc
    user: User
    user_index: int


class CoverageDetails(TypedDict):
    """Bidirectional coverage audit payload."""

    declared_classes: list[str]
    exercised_classes: list[str]
    declared_exit_vectors: list[str]
    exercised_exit_vectors: list[str]
    trajectory_count: int
    arc_count: int
    # The DISTINCT arcs that produced a scored item. Published beside trajectory_count because the
    # two differ by the fixture's user count while the arc payload and the oracle are identical
    # across users: N scored items are not N independent arcs, so a reader comparing runs by the
    # published interval (docs/scoring.md § Noise) needs to see both numbers.
    distinct_arcs_measured: int
    per_class_arc_count: dict[str, int]
    class_to_dimension: dict[str, str]


class DependencyBreachRecord(TypedDict):
    """One trajectory on the human-review worklist."""

    test_case_id: str
    category: str
    exit_vector: str
    arc_id: str
    user: str
    failed_dimensions: list[str]


class DependencyBreachDetails(TypedDict):
    flagged_count: int
    scored_count: int
    # Scored arcs that FAILED but carry no rubric verdict to attribute the failure to. Counted
    # rather than silently skipped: such an arc scores 0.0 and would otherwise be invisible on the
    # worklist an operator is told to read instead of the percentage.
    verdictless_failure_count: int
    flagged: list[DependencyBreachRecord]


class M12TrajectoryDetails(TrajectoryDetails):
    """Evidence-item details for one scored M12 arc."""

    arc_id: str
    exit_vector: str
    # Characters in the transcript handed to the judge. Published so a reader can see how close an
    # arc ran to the payload ceiling that would have truncated its exit turns.
    blob_chars: int


class M12JudgeTemperatureError(RuntimeError):
    """Raised when a non-ensemble judge is configured at temperature > 0 (non-deterministic)."""
