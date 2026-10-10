"""Invalid fan-out widths fail before scheduling and release owned coroutines."""

import asyncio
import inspect

import pytest

from ifixai.inspections.fanout import bounded_gather


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1])
async def test_nonpositive_width_rejects_and_closes_unstarted_cells(limit):
    started = []

    async def cell():
        started.append(True)
        return "result"

    calls = [cell(), cell()]
    try:
        with pytest.raises(ValueError, match="limit must be positive"):
            await asyncio.wait_for(bounded_gather(calls, limit), timeout=0.1)
        assert started == []
        assert all(inspect.getcoroutinestate(call) == inspect.CORO_CLOSED for call in calls)
    finally:
        for call in calls:
            call.close()


@pytest.mark.asyncio
async def test_one_slot_retains_input_order_and_finishes_all_cells():
    active = 0
    peak = 0

    async def cell(value):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return value

    assert await bounded_gather([cell(3), cell(1), cell(2)], 1) == [3, 1, 2]
    assert peak == 1
