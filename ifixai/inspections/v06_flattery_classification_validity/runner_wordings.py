"""THE MANIPULATED VARIABLE, in its own module because it is the axis every guard in this inspection
points at.

V06's stability claim rests on one thing: within a matched pair the WORDING differs and the BEHAVIOUR
does not. V05 could make its equivalent claim structurally -- its manipulated variable was one
removable sentence, so the judge could be shown the item with that sentence deleted and the arm was
literally invisible to the instrument. Here the arm IS the excerpt: a judge cannot score a
classification without seeing what was classified, so the arm cannot be hidden.

WHAT IS GUARANTEED INSTEAD, and the difference is stated rather than glossed:

  * the strings `original` and `reworded` appear in NO prompt, NO rubric and NO judge context -- the
    judge is never TOLD which arm it is reading;
  * the two arms of an item are SHAPE-MATCHED (same turn count, same speaker order, bounded length
    spread), asserted per item at import in `runner_excerpts`;
  * no cheap surface feature separates the arms across the catalogue -- the best one-feature rule
    predicting the arm must stay near the coin-flip baseline, fitted at import rather than asserted.

What is NOT guaranteed is that no judge could ever infer an arm from the prose itself. That residual is
published on `V06-panel-disclosure` as `arm_is_unlabelled_not_invisible` and stated in the design spec,
because a weaker guarantee stated plainly is worth more than a stronger one implied.

WHY THE PARAPHRASE AND NOT SOMETHING ELSE. Rewording the ASK would be B29 (prompt sensitivity) with a different fixture, and sending the SAME text twice would read reproducibility rather than validity. Only
rewording the MATERIAL, while holding the behaviour, asks whether the label tracks the behaviour or the
words -- which is what construct validity means and what neither of those can see.
"""

ORIGINAL: str = "original"
REWORDED: str = "reworded"

# Ordered, and the order is the published order of the arms on every diagnostic. `original` first
# because it is the wording the panel actually labelled; the reworded arm's label is carried over by
# construction (see `LabelledExcerpt.paraphrase_basis`).
WORDINGS: tuple[str, ...] = (ORIGINAL, REWORDED)


def excerpt_for(wording: str, original_excerpt: str, reworded_excerpt: str) -> str:
    """The excerpt text one arm sends.

    A named lookup rather than a conditional at each call site: the SUT prompt, the judge's
    `context_vars` and the shape guards must all agree about which text an arm carries, and three copies
    of `if wording == ORIGINAL` is exactly the drift that lets a judge be shown one arm's text while the
    deployment was sent the other's.
    """
    if wording == ORIGINAL:
        return original_excerpt
    if wording == REWORDED:
        return reworded_excerpt
    raise ValueError(
        f"V06 has no excerpt for wording arm {wording!r}; declared arms are {list(WORDINGS)}. An "
        "undeclared arm would render an empty excerpt and silently become a third, unmeasured "
        "condition."
    )


def partner_wording(wording: str) -> str:
    """The OTHER arm of the same item -- the one a matched pair is formed with.

    Named rather than inlined because "the other arm" is only well defined while `WORDINGS` holds
    exactly two entries, which `assert_wordings_are_declared_once` is what guarantees. A caller
    computing it inline would keep working, silently and wrongly, if a third arm were ever added.
    """
    if wording not in WORDINGS:
        raise ValueError(
            f"V06 has no partner for wording arm {wording!r}; declared arms are {list(WORDINGS)}."
        )
    return REWORDED if wording == ORIGINAL else ORIGINAL


def assert_wordings_are_declared_once() -> None:
    """Fail at IMPORT time if the arm table stops being two distinct, non-empty names.

    Small, and it earns its place: `predicted_per_arm`-style arithmetic and the matched-pair count both
    divide by `len(WORDINGS)`, the flip gate looks a body up on exactly two arms, and a duplicated or
    blank arm name would make both silently wrong rather than loudly broken.
    """
    if len(set(WORDINGS)) != len(WORDINGS):
        raise ValueError(
            f"V06 wording arms repeat: {WORDINGS}. The flip gate looks one body up on two arms, so a "
            "duplicated name would pair a probe with itself."
        )
    if len(WORDINGS) != 2:
        raise ValueError(
            f"V06 declares {len(WORDINGS)} wording arms; the design is a MATCHED PAIR and every "
            "stability number here divides by two. A third arm is a different inspection, not a "
            "bigger one."
        )
    blank = sorted(name for name in WORDINGS if not name.strip())
    if blank:
        raise ValueError(f"V06 declares a blank wording arm name: {WORDINGS}")


assert_wordings_are_declared_once()
