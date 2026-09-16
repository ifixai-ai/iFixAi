"""The four dispositions, and nothing else.

The disposition is THE THING HELD CONSTANT inside a matched case: the same trait is put to the
deployment twice, once in a scenario category a public benchmark publishes and once in one drawn from
outside that set. If the held-out arm required a DIFFERENT trait, the gap between the arms would
measure breadth of competence rather than transfer, and V07 would be a worse version of four
inspections that already exist.

WHY THESE FOUR. Each is (a) checkable in a single reply, (b) expressible in an ordinary operational
scenario as readily as in a canonical benchmark one -- which is what makes a matched case possible at
all -- and (c) a trait whose published-benchmark form is genuinely canonical, so the in-category arm is
a shape a model has seen a great many times. A trait with no canonical published form would leave the
in-category arm no more familiar than the held-out one, and the manipulated variable would not be
manipulated.

WHAT V07 DOES *NOT* CLAIM ABOUT THEM. The per-disposition pass rates are published for attribution, not
as a verdict on whether the deployment is good at any of these traits; that reading belongs to the
inspections that own each one. V07's finding is the RELATION between two arms with the
disposition held fixed.

These four strings reach `EvidenceItem.details["category"]` -- the shared attribution key every
taxonomy-bearing inspection uses, so the shared coverage helpers and
`ScoreBreakdown.per_category_pass_rate` read V07 without a special case. They reach no prompt, no
rubric and no judge context: a judge told which trait a scenario is shaped around would be grading a
category rather than the reply in front of it.
"""

__all__ = [
    "ADVERSE_DISCLOSURE",
    "DECISION_ROUTING",
    "DISPOSITIONS",
    "MANDATE_RESTRAINT",
    "UNRESOLVED_DISCLOSURE",
]

# Do what was asked and decline the adjacent action nobody authorised, naming the boundary rather than
# silently stopping short of it.
MANDATE_RESTRAINT: str = "mandate_restraint"

# Say plainly what is not settled instead of supplying a confident value that covers the gap.
UNRESOLVED_DISCLOSURE: str = "unresolved_disclosure"

# Hand a decision that belongs to a named person back to them, with what they need to take it.
DECISION_ROUTING: str = "decision_routing"

# Volunteer the fact that cuts against the course being recommended.
ADVERSE_DISCLOSURE: str = "adverse_disclosure"

# Ordered, and the order is the published one, so per-disposition tables read the same way everywhere.
DISPOSITIONS: tuple[str, ...] = (
    MANDATE_RESTRAINT,
    UNRESOLVED_DISCLOSURE,
    DECISION_ROUTING,
    ADVERSE_DISCLOSURE,
)
