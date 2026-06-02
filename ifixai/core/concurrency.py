import asyncio
from contextlib import asynccontextmanager
from typing import TypedDict

JUDGE_CALL_CAP = 200
RATE_LIMIT_RECOVERY_SECONDS = 30.0
MIN_EFFECTIVE_LIMIT = 1
MAX_CONCURRENCY_LIMIT = 20
_RAMP_INTERVAL_SECONDS = 0.5


class InspectionConcurrencyLimits(TypedDict):
    b05: int
    b07: int
    b08: int
    b09: int
    b10: int
    b12: int
    b14: int
    b17: int
    b19: int
    b20: int
    b22: int
    b28: int
    b29: int
    b30: int
    b31: int
    b32: int


DEFAULT_INSPECTION_CONCURRENCY: InspectionConcurrencyLimits = {
    "b05": 12,
    "b07": 12,
    "b08": 12,
    "b09": 12,
    "b10": 12,
    "b12": 8,
    "b14": 12,
    "b17": 12,
    "b19": 8,
    "b20": 8,
    "b22": 12,
    "b28": 12,
    "b29": 12,
    "b30": 8,
    "b31": 12,
    "b32": 12,
}


class JudgeCallCapExceeded(RuntimeError):

    def __init__(self, calls_used: int, cap: int) -> None:
        super().__init__(
            f"Judge-call cap exceeded: {calls_used}/{cap} calls already used. "
            f"Reduce --test scope or switch to a non-judge eval mode."
        )
        self.calls_used = calls_used
        self.cap = cap


async def _recover_effective_limit(governor: "ConcurrencyGovernor") -> None:
    await asyncio.sleep(RATE_LIMIT_RECOVERY_SECONDS)
    async with governor._rate_limit_lock:
        governor.effective_limit = governor.configured_limit
        governor._throttled = False
        governor._recovery_task = None
    # Wake waiters one at a time so they don't all burst at once and cause another 429.
    for _ in range(governor.configured_limit):
        async with governor._throttle_cond:
            governor._throttle_cond.notify(1)
        await asyncio.sleep(_RAMP_INTERVAL_SECONDS)
    # Release any waiters that built up beyond configured_limit.
    async with governor._throttle_cond:
        governor._throttle_cond.notify_all()


class ConcurrencyGovernor:

    def __init__(
        self, configured_limit: int, judge_call_cap: int = JUDGE_CALL_CAP
    ) -> None:
        if not (1 <= configured_limit <= MAX_CONCURRENCY_LIMIT):
            raise ValueError(
                f"configured_limit must be between 1 and {MAX_CONCURRENCY_LIMIT} "
                f"(got {configured_limit})"
            )
        self.configured_limit = configured_limit
        self.effective_limit = configured_limit
        self._semaphore = asyncio.Semaphore(configured_limit)
        self._throttled: bool = False
        self._throttle_cond = asyncio.Condition()
        self._judge_calls_used = 0
        self._judge_call_cap = judge_call_cap
        self._rate_limit_lock = asyncio.Lock()
        self._cap_lock = asyncio.Lock()
        self._recovery_task: asyncio.Task | None = None

    @asynccontextmanager
    async def acquire(self):
        async with self._throttle_cond:
            await self._throttle_cond.wait_for(lambda: not self._throttled)
        async with self._semaphore:
            yield

    async def reserve_judge_call(self) -> None:
        async with self._cap_lock:
            if self._judge_calls_used >= self._judge_call_cap:
                raise JudgeCallCapExceeded(self._judge_calls_used, self._judge_call_cap)
            self._judge_calls_used += 1

    async def on_rate_limit(self) -> None:
        async with self._rate_limit_lock:
            new_limit = max(MIN_EFFECTIVE_LIMIT, self.effective_limit // 2)
            if new_limit != self.effective_limit:
                self.effective_limit = new_limit
            self._throttled = True
            if self._recovery_task is not None and not self._recovery_task.done():
                self._recovery_task.cancel()
            self._recovery_task = asyncio.create_task(_recover_effective_limit(self))

    def remaining_budget(self) -> int:
        return self._judge_call_cap - self._judge_calls_used

    @property
    def is_sequential(self) -> bool:
        return self.configured_limit == 1
