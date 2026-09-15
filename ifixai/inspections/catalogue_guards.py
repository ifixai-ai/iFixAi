"""Import-time guards over a runner-authored catalogue.

An inspection whose material is authored in Python rather than read from a fixture carries a hazard
the fixture path does not: the author can, without meaning to, build the answer into the shape of the
items. A catalogue whose long items all carry one label, or whose every paraphrase is shorter than its
partner, hands a deployment (or a judge) a rule that reaches the right answer without doing the work —
and every number the inspection then publishes is measuring the catalogue rather than the deployment.

The defence is the same in every inspection that has needed it: define a handful of CHEAP SURFACE
FEATURES, fit the best one-feature rule from each onto the target, and refuse to import when any of
them beats a declared ceiling. V04, V05 and V06 each carry a private copy of that fit, which is the
drift this module exists to prevent — a fix to the arithmetic would otherwise have to be made three
times and could be made once.

**What stays per-inspection, and why.** The FEATURES, the TARGET, the CEILING and the raising are all
inspection-specific: the features are the shortcuts that particular material could offer, the ceiling
is argued against that inspection's own baseline (a coin for a two-arm axis, 1/3 for three labels),
and the error message is where the reasoning a reader needs actually lives. Only the fit is shared.

No I/O and no global state: both functions are pure over their arguments.
"""

from collections import Counter

__all__ = [
    "best_rule_accuracy",
    "diagnostic_features",
]


def best_rule_accuracy(flags: list[bool], targets: list[str]) -> float:
    """Accuracy of the best one-feature rule mapping `flags` onto `targets`.

    A one-feature rule assigns one target to the feature-present group and one to the feature-absent
    group, and an adversary would pick the majority target of each — which is what this computes. It
    is the ADVERSARY'S accuracy rather than the feature's correlation, because the question a guard
    asks is "could someone read the answer off this?", not "is this correlated?".

    Generic over the target, so a label guard and an arm guard fit the SAME function rather than two
    copies that could drift apart while each believed the other was checking.

    Returns 0.0 on an empty catalogue rather than dividing by zero: a guard over nothing has found no
    diagnostic feature, and the emptiness is a separate assertion's business.

    `strict=True` on the zip: `flags` and `targets` are always built from the same sample list, so a
    length mismatch is a caller bug rather than a shape to tolerate -- and silently truncating to the
    shorter one would fit the rule over a subset while reporting it as the whole catalogue, which is
    the one failure mode a guard must never have.
    """
    if not flags:
        return 0.0
    correct = 0
    for value in (True, False):
        group = [t for flag, t in zip(flags, targets, strict=True) if flag is value]
        if group:
            correct += Counter(group).most_common(1)[0][1]
    return correct / len(flags)


def diagnostic_features(
    flags_by_name: dict[str, list[bool]],
    targets: list[str],
    ceiling: float,
) -> dict[str, float]:
    """The features whose best one-feature rule beats `ceiling`, with their accuracies, rounded.

    Returns `{}` when the catalogue is clean, so a caller reads it as a falsy guard result and raises
    with its own message. The message stays at the call site deliberately: what a diagnostic feature
    MEANS differs by inspection — on one axis it is a deployment shortcut, on another it is a judge
    that could tell two arms apart — and a shared error string would say neither.
    """
    return {
        name: round(accuracy, 4)
        for name, flags in flags_by_name.items()
        if (accuracy := best_rule_accuracy(flags, targets)) > ceiling
    }
