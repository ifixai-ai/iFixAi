import asyncio

import pytest

from ifixai import api
from ifixai.core import runner
from ifixai.core.concurrency import ConcurrencyGovernor
from ifixai.core.types import ProviderCapabilities, TestResult
from ifixai.evaluation.errors import JudgeUnavailableError
from ifixai.providers.base import ChatProvider


class OwnedProvider(ChatProvider):
    def __init__(self):
        self.closed = False
        self.calls_after_close = 0

    async def send_message(self, messages, config):
        if self.closed:
            self.calls_after_close += 1
        return "owned synthetic response"

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("termination", ["judge", "callback", "cancel"])
async def test_parallel_abort_drains_inspections_before_provider_close(
    monkeypatch, termination
):
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    owned_tasks = []
    provider = OwnedProvider()

    class PendingInspection:
        async def execute(self, provider, config, *args, **kwargs):
            owned_tasks.append(asyncio.current_task())
            started.set()
            try:
                await release.wait()
                await provider.send_message([], config)
                return TestResult(test_id="B02", name="pending", score=1)
            finally:
                # An asynchronous cleanup must finish before aclose as well.
                await asyncio.sleep(0)
                finished.set()

    class FirstInspection:
        async def execute(self, *args, **kwargs):
            await started.wait()
            if termination == "judge":
                raise JudgeUnavailableError("owned judge exhausted retries")
            if termination == "cancel":
                await asyncio.Event().wait()
            return TestResult(test_id="B01", name="completed", score=1)

    async def capabilities(*args):
        return ProviderCapabilities()

    def progress(*args):
        if termination == "callback":
            raise RuntimeError("owned callback failed")

    monkeypatch.setattr(runner, "detect_capabilities", capabilities)
    monkeypatch.setattr(
        runner,
        "INSPECTION_REGISTRY",
        {
            "B01": FirstInspection(),
            "B02": PendingInspection(),
        },
    )
    task = asyncio.create_task(
        api.run_selected(
            {"B01", "B02"},
            provider,
            fixture="default",
            governor=ConcurrencyGovernor(2),
            progress_callback=progress,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        if termination == "cancel":
            task.cancel()
        expected = {
            "judge": JudgeUnavailableError,
            "callback": RuntimeError,
            "cancel": asyncio.CancelledError,
        }[termination]
        with pytest.raises(expected):
            await task
        assert provider.closed
        assert finished.is_set(), (
            "inspection remains alive after API closes its provider"
        )
        assert all(t.done() for t in owned_tasks)
        release.set()
        await asyncio.sleep(0)
        assert provider.calls_after_close == 0
    finally:
        release.set()
        task.cancel()
        for pending in owned_tasks:
            pending.cancel()
        await asyncio.gather(task, *owned_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_parallel_success_preserves_results_and_callback_order():
    completed = []

    class Inspection:
        def __init__(self, test_id, fails=False):
            self.test_id = test_id
            self.fails = fails

        async def execute(self, *args, **kwargs):
            if self.fails:
                raise ValueError("ordinary inspection failure")
            return TestResult(test_id=self.test_id, name="owned", score=1)

    from ifixai.core.fixture_loader import load_fixture
    from ifixai.core.types import ProviderConfig

    results = await runner._execute_inspections(
        {"B02": Inspection("B02", fails=True), "B01": Inspection("B01")},
        runner.ALL_SPECS,
        OwnedProvider(),
        ProviderConfig(provider="custom"),
        load_fixture("default"),
        ProviderCapabilities(),
        progress_callback=lambda test_id, index, total, result: completed.append(
            (test_id, index, total)
        ),
        governor=ConcurrencyGovernor(2),
    )
    assert [r.test_id for r in results] == ["B01", "B02"]
    assert results[0].error is None
    assert "ordinary inspection failure" in results[1].error
    assert [index for _, index, _ in completed] == [1, 2]
    assert all(total == 2 for _, _, total in completed)
