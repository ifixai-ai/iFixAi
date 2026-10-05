import asyncio

import pytest

from ifixai.core.concurrency import ConcurrencyGovernor


@pytest.mark.asyncio
async def test_waiter_queued_before_rate_limit_does_not_issue_during_cooldown():
    governor = ConcurrencyGovernor(1)
    entered = asyncio.Event()

    async def wait():
        async with governor.acquire():
            entered.set()

    async with governor.acquire():
        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0)
        await governor.on_rate_limit()
    try:
        await asyncio.sleep(0.01)
        assert not entered.is_set(), (
            "a queued request bypassed the active rate-limit cooldown"
        )
        async with governor._throttle_cond:
            governor._throttled = False
            governor._throttle_cond.notify_all()
        await asyncio.wait_for(waiter, 1)
        assert entered.is_set()
    finally:
        if governor._recovery_task:
            governor._recovery_task.cancel()
            await asyncio.gather(governor._recovery_task, return_exceptions=True)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_cooldown_waiter_does_not_leak_permit():
    governor = ConcurrencyGovernor(1)
    entered = asyncio.Event()

    async def acquire_again():
        async with governor.acquire():
            entered.set()

    async with governor.acquire():
        waiter = asyncio.create_task(acquire_again())
        await asyncio.sleep(0)
        await governor.on_rate_limit()
    try:
        # The queued waiter acquires the released semaphore, then must wait at
        # the second throttle gate while it holds the permit.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert governor._semaphore._value == 0
        assert not entered.is_set()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        async with governor._throttle_cond:
            governor._throttled = False
            governor._throttle_cond.notify_all()
        await asyncio.wait_for(acquire_again(), 1)
        assert entered.is_set()
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        governor._recovery_task.cancel()
        await asyncio.gather(governor._recovery_task, return_exceptions=True)
