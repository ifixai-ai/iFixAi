"""Bounded parallel probing for inspection sweeps whose cells are independent.

An inspection that sweeps a grid — every (arc, user), every (user, tool) — issues one
provider call per cell and no cell reads another's result. Those cells are latency-bound
on the provider, so they fan out. Two rules make that safe:

* a semaphore caps how many cells are in flight, so one inspection cannot spend the
  whole run's in-flight call allowance on itself;
* results come back in input order and the gather is drained through
  :func:`raise_first_error`, so evidence stays positionally stable and which failure
  surfaces is deterministic.

Not every sweep qualifies. A sweep whose cells share mutable state (a judge circuit
breaker's consecutive-failure counter, a session the next cell must find untouched, a
tamper window a later cell must observe) is load-bearing serial and must stay that way.
"""

import asyncio
import inspect
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

    `gather` schedules every coroutine as a task up front, so nothing is left un-awaited on
    failure; the semaphore only staggers when each body runs. A CANCELLED sweep is the
    exception, and `close_unstarted` handles it.
    """
    semaphore = asyncio.Semaphore(limit)
    try:
        outcomes = await asyncio.gather(
            *[run_under_slot(semaphore, call) for call in calls],
            return_exceptions=True,
        )
    finally:
        close_unstarted(calls)
    return raise_first_error(list(outcomes))


def close_unstarted(calls: Sequence[Coroutine[Any, Any, T]]) -> None:
    """Close every cell coroutine that never got a slot.

    When the sweep is cancelled — Ctrl-C, or the run stopped because a judge is unreachable —
    the cells still queued on the semaphore are cancelled before their body starts, so their
    coroutines were created but never awaited and asyncio reports each one at shutdown
    ("coroutine ... was never awaited"). Only never-started coroutines are closed; a running
    one is finished by its own cancellation, and on a completed sweep every cell is already
    closed.
    """
    for call in calls:
        if inspect.getcoroutinestate(call) == inspect.CORO_CREATED:
            call.close()


def raise_first_error(outcomes: list[T | BaseException]) -> list[T]:
    """Return gathered results, re-raising the lowest-indexed failure.

    Pairs with ``asyncio.gather(..., return_exceptions=True)``. A bare gather surfaces the
    first failure *in time* and leaves the sibling coroutines running — on a dead provider
    they keep issuing billable calls that nobody will read, and asyncio later reports them
    as never-retrieved. Draining every outcome first and then raising by index costs
    nothing on the happy path and makes which error surfaces deterministic.
    """
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]
