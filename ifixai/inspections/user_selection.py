"""Shared user selection for the (user x probe) structural inspections (M02, M03, M06, M07).

Every inspection in this family enumerates a cross-product of the fixture's users against a
frozen probe corpus, and keys each evidence item on `user_id`. That makes the selection of
users part of the evidence record's integrity, not a detail of the sweep, so all four run it
through the same two functions here rather than each slicing `fixture.users` its own way.

Nothing in here affects a score: every selected user runs every probe, and no probe's verdict
depends on who issued it. What it governs is whether the record is self-consistent and stable.
"""

import logging
from collections import Counter
from collections.abc import Sequence

from ifixai.core.types import User

DUPLICATE_USER_ID_WARNING = (
    "%s fixture repeats user_id(s) %s; evidence ids are keyed on user_id, so only the first "
    "user under each id is audited (a repeat would collide with an already-audited principal)"
)


def capped_unique_users(users: Sequence[User], max_users: int) -> list[User]:
    """The users an inspection audits: sorted by `user_id`, de-duplicated on it, then capped.

    All three steps, in that order, and each earns its place:

    SORT FIRST, because a bare `fixture.users[:max_users]` slice selects in FILE order, so two
    fixtures carrying the same users in a different order audit different users and emit
    different evidence ids — deterministic per file, but not stable under a harmless reshuffle,
    which is weaker than the byte-stability these inspections claim.

    The sort is LEXICOGRAPHIC on `user_id`, which is worth stating because it surprises people
    above the cap: a twelve-user fixture `u1..u12` audits `u1, u10, u11, u12, u2`, not the first
    five in the file. That is the price of reshuffle-stability and it costs nothing measurable
    (every selected user runs every probe), but an operator comparing the audited set against
    their YAML needs to know the rule before they file a bug about it.

    DE-DUPLICATE on `user_id`, because it is the evidence id's only user-varying component and
    `Fixture` does not constrain it to be unique. Two users sharing an id produce colliding
    `test_case_id`s — the record's primary key — so downstream joins silently lose rows, and a
    coverage summary's `user_count` (a set over `user_id`) reports fewer users than actually
    ran, i.e. a diagnostic states a number that is false. Two distinct principals under one id
    are unrepresentable in the evidence record however this is resolved; keeping the first and
    warning is the option that leaves the record self-consistent. Rejected alternatives: putting
    an index in the id changes every id for no gain on well-formed fixtures, and raising in the
    fixture gate would make these inspections the only ones that reject a schema-valid fixture
    their siblings accept.

    CAP LAST, so the cap counts what will actually run.

    Deliberately NOT `harness.base.sample_capped`, though that helper also sorts-then-caps:
    above the cap it draws a SEEDED subsample, and M03/M06/M07 are seed-free by construction.
    Using it would reintroduce the RNG branch that design exists to avoid, and it does not
    de-duplicate. (M02 keeps `sample_capped` for its item-level cap, which is a separate
    decision applied after this one.)

    Pure and side-effect free: a runner's `execute` calls it to predict the evidence count and
    its `run` calls it again to enumerate, so a log line in here would fire twice and read as
    two separate drops. The warning lives at the single `run` call site instead — see
    `warn_on_duplicate_user_ids`.
    """
    seen: set[str] = set()
    unique: list[User] = []
    # (user_id, name) rather than user_id alone: among users sharing an id, a key of user_id
    # alone leaves the retained principal to the stable sort, i.e. to fixture FILE order — the
    # same instability the sort exists to remove, merely pushed from the id onto the name
    # recorded in the evidence.
    for user in sorted(users, key=lambda candidate: (candidate.user_id, candidate.name)):
        if user.user_id not in seen:
            seen.add(user.user_id)
            unique.append(user)
    return unique[:max_users]


def warn_on_duplicate_user_ids(
    logger: logging.Logger,
    inspection_id: str,
    users: Sequence[User],
    max_users: int,
) -> None:
    """Warn once, naming every repeated `user_id` that actually cost a principal its place.

    Takes the caller's logger so the record carries the inspection's own logger name — an
    operator filtering on `ifixai.inspections.m07_cross_org_delegation_scope` still sees it.

    `capped_unique_users` drops the repeats silently by design — see its docstring for why
    dropping beats the alternatives — but a silent drop would leave the operator wondering why
    their six-user fixture reports five, so the reason is stated once per run rather than once
    per dropped user.

    Scoped to the AUDITED ids, not to the whole fixture: only `max_users` users are ever
    audited, so a duplicate sitting outside the cap costs nothing — the second entry was never
    going to be probed. Warning about it would send the operator to fix a fixture whose audit is
    unaffected.
    """
    audited = {user.user_id for user in capped_unique_users(users, max_users)}
    occurrences = Counter(user.user_id for user in users)
    repeated = sorted(
        user_id
        for user_id, count in occurrences.items()
        if count > 1 and user_id in audited
    )
    if repeated:
        logger.warning(DUPLICATE_USER_ID_WARNING, inspection_id, repeated)
