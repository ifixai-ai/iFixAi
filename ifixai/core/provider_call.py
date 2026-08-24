"""The one way the engine talks to a provider.

Every outbound LLM call goes through :func:`send_governed`, which applies the
run's concurrency ceiling, waits out any rate-limit recovery window, and records
the call for `ifixai perf-report` — in that order, so a throttled run stops
issuing calls rather than queueing them behind a held slot.

Calling ``provider.send_message`` directly from engine code bypasses all three.
`tests/unittesting/core/test_all_calls_are_governed.py` enforces this: a new
direct call site fails the gate.

Capability hooks (`retrieve_sources`, `invoke_tool`, the gate surfaces) are the
same problem wearing a different name — most have in-process default
implementations, but any provider may back one with a remote call. Those go
through :func:`call_governed`, and the same gate module enforces it for every
hook whose shipped implementation actually reaches the network.
"""

from collections.abc import Coroutine
from typing import Any, TypeVar

from ifixai.core.concurrency import hold_call_slot, signal_active_rate_limit
from ifixai.core.instrumentation import CallRole, trace_call
from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ChatProvider, ProviderRateLimitError

T = TypeVar("T")


async def send_governed(
    provider: ChatProvider,
    messages: list[ChatMessage],
    config: ProviderConfig,
    role: CallRole = "sut",
) -> str:
    """Send one chat completion under the run's concurrency and tracing controls.

    ``role`` splits the perf report into the SUT half and the judge half; it does
    not change how the call is made.
    """
    async with hold_call_slot(), trace_call(role):
        return await provider.send_message(messages, config)


async def call_governed(
    capability_call: Coroutine[Any, Any, T], role: CallRole = "sut"
) -> T:
    """Run one provider capability call under the same controls as a chat call.

    Takes the un-started coroutine rather than a method and its arguments:
    coroutines do not run until awaited, so the call still begins inside the
    slot, and the caller keeps its own argument types instead of forwarding
    through ``*args``.

        sources = await call_governed(provider.retrieve_sources(query, config))

    The coroutine is closed if the slot is never acquired — waiting on the 429
    gate is a cancellation point, and unlike :func:`send_governed` (which builds
    its coroutine inside the block) this one is handed a coroutine that would
    otherwise be garbage-collected un-started, emitting a "never awaited"
    warning during shutdown. Typed as ``Coroutine`` rather than ``Awaitable``
    precisely so ``close()`` is part of the contract.
    """
    try:
        async with hold_call_slot(), trace_call(role):
            return await capability_call
    except BaseException:
        # No-op once the coroutine has started; suppresses the warning when it
        # has not.
        capability_call.close()
        raise


async def signal_if_rate_limited(exc: BaseException) -> None:
    """Tell the governor about a 429 that the caller is handling locally.

    An inspection that isolates a per-probe failure — catching broadly so one
    dead cell cannot sink its sibling fan-out — still has to surface a rate
    limit, because the remedy is run-wide. Swallowing it leaves the governor
    admitting calls at full concurrency into a limit the caller already knows
    about, and every sibling inspection then rediscovers it the slow way.

    Safe to call with any exception; a non-rate-limit is a no-op.
    """
    if isinstance(exc, ProviderRateLimitError):
        await signal_active_rate_limit()
