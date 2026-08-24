"""Bounded parallel probing for inspections whose cells are independent.

An inspection that sweeps a grid — every (user, role, tool), every (user, tool) —
issues one provider call per cell and no cell reads another's result. Those runs
are latency-bound on the provider, not on anything the inspection computes, so
they fan out. Two rules make that safe:

* a semaphore caps how many cells are in flight, so one inspection cannot spend
  the whole run's in-flight call allowance on itself;
* results come back in input order, so evidence stays positionally stable and a
  fanned-out inspection scores identically to its serial predecessor.

Not every sweep qualifies. A sweep whose cells share mutable state (a judge
circuit breaker's consecutive-failure counter, a session the next cell must find
untouched, a tamper window a later cell must observe) is load-bearing serial and
must stay that way — see the comments at those call sites.
"""

import asyncio
from collections.abc import Coroutine, Sequence
from typing import Any, TypeVar

T = TypeVar("T")


async def run_under_slot(
    semaphore: asyncio.Semaphore, call: Coroutine[Any, Any, T]
) -> T:
    """Await one cell's coroutine while holding a fan-out slot."""
    async with semaphore:
        return await call


async def bounded_gather(
    calls: Sequence[Coroutine[Any, Any, T]], limit: int
) -> list[T]:
    """Run independent cells concurrently under a width cap; results in input order.

    The one fan-out idiom for inspection sweeps. Before this existed, every
    runner carried the same three-part block — build a semaphore, wrap each cell
    in a `_bounded_*` method that acquires it, drain the gather through
    :func:`raise_first_error` — ~25 copies, each a chance to forget
    ``return_exceptions=True`` and leak siblings on a dead provider.

    `gather` schedules every coroutine as a task up front, so nothing is left
    un-awaited on failure; the semaphore only staggers when each body runs.
    """
    semaphore = asyncio.Semaphore(limit)
    outcomes = await asyncio.gather(
        *[run_under_slot(semaphore, call) for call in calls],
        return_exceptions=True,
    )
    return raise_first_error(list(outcomes))


def raise_first_error(outcomes: list[T | BaseException]) -> list[T]:
    """Return gathered results, re-raising the lowest-indexed failure.

    Pairs with ``asyncio.gather(..., return_exceptions=True)``. A bare gather
    surfaces the first failure *in time* and leaves the sibling coroutines
    running — on a dead provider they keep issuing billable calls that nobody
    will read, and asyncio later reports them as never-retrieved. Draining every
    outcome first and then raising by index costs nothing on the happy path and
    makes which error surfaces deterministic.
    """
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
