"""The two arms of every matched case, and nothing else.

One home for the arm vocabulary, so the runner, the split filter, the gates, the floors and the
diagnostics all name the axis through a single import. A second literal `"held_out"` written somewhere
else is how an arm read silently starts scoping to a population that does not exist.

THESE TWO STRINGS REACH NO PROMPT, NO RUBRIC AND NO JUDGE CONTEXT. They are stamped on
`EvidenceItem.details["arm"]` for attribution only. A deployment that could tell which arm a probe was
in could answer the two differently on purpose; a judge that could tell would be free to hold them to
different standards, and the published transfer gap would then be an artefact of the instrument rather
than a finding about the deployment. Pinned by test_no_split_vocabulary_reaches_the_sut_prompt and
test_the_arm_never_reaches_the_judge.
"""

__all__ = [
    "ARMS",
    "HELD_OUT",
    "IN_CATEGORY",
    "partner_arm",
]

# The arm whose scenario category is one a public benchmark publishes -- the surface a model has seen.
IN_CATEGORY: str = "in_category"

# The arm whose scenario category sits outside that published set while demanding the same disposition.
HELD_OUT: str = "held_out"

# Ordered, and the order is the published one: in-category first, because `transfer_gap` is defined as
# in-category minus held-out and a reader meeting the pair should meet it in the same order the
# subtraction takes it.
ARMS: tuple[str, str] = (IN_CATEGORY, HELD_OUT)


def partner_arm(arm: str) -> str:
    """The other arm of the same matched case.

    Used by the arm-separability guard, which asks of each probe whether it is longer than ITS OWN
    partner rather than longer than the catalogue median -- the per-case comparison is the one that
    catches an author who lengthened every held-out scenario, which is the realistic drift.
    """
    return HELD_OUT if arm == IN_CATEGORY else IN_CATEGORY
