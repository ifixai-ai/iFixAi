"""A concurrent future does not establish synchronous action completion."""

from concurrent.futures import Future

import pytest

from ifixai.policy_lab import create_exploration_bundle
from ifixai.policy_lab.executor import execute_witness


@pytest.mark.parametrize("state", ["pending", "failed", "completed"])
def test_future_action_return_cannot_establish_completed_replay(native_example, state):
    bundle = create_exploration_bundle(native_example.demo_model(), native_example.demo_policy())
    store = native_example.LocalRefundStore()
    future = Future()
    if state == "failed":
        future.set_exception(RuntimeError("private background failure"))
    elif state == "completed":
        future.set_result(None)

    def background_request(context):
        store.request(context)
        return future

    bindings = {**store.bindings(), "01_request": background_request}
    report = execute_witness(bundle, bundle.report.safety_witnesses[0], bindings, store.reset, store.observe)
    assert report.decision == "EXECUTION_ERROR"
    assert report.attempted_actions == 1
    assert report.completed_actions == 0
    assert store.calls == ["01_request"]
    assert report.steps[0].after["requested"] is True
    assert "private background failure" not in report.model_dump_json()
