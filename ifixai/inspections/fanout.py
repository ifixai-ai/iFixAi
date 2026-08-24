"""Deterministic error surfacing for inspection sweeps whose cells are independent.

An inspection that sweeps a grid — every (arc, user), every (user, tool) — issues one
provider call per cell and no cell reads another's result. Those cells are latency-bound
on the provider, so they fan out under a semaphore the runner owns. Draining the gather
through :func:`raise_first_error` is what makes the failure mode of such a sweep
deterministic.

Not every sweep qualifies. A sweep whose cells share mutable state (a judge circuit
breaker's consecutive-failure counter, a session the next cell must find untouched, a
tamper window a later cell must observe) is load-bearing serial and must stay that way.
"""

from typing import TypeVar

T = TypeVar("T")


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
