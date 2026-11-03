"""Callbacks must not replace the bindings admitted before replay starts."""

import pytest

from ifixai.policy_lab import create_exploration_bundle
from ifixai.policy_lab.executor import execute_witness


@pytest.mark.parametrize("mutation_stage", ["reset", "observer", "action"])
def test_replay_uses_prevalidated_binding_snapshot(native_example, mutation_stage):
    bundle = create_exploration_bundle(native_example.demo_model(), native_example.demo_policy())
    witness = bundle.report.safety_witnesses[0]
    store = native_example.LocalRefundStore()
    bindings = store.bindings()
    substituted = []

    def replacement(context):
        substituted.append(context.action_id)
        store.pay(context)

    def mutate():
        bindings["04_pay"] = replacement

    def reset():
        store.reset()
        if mutation_stage == "reset":
            mutate()

    def observe():
        if mutation_stage == "observer":
            mutate()
        return store.observe()

    request = bindings["01_request"]

    def action(context):
        request(context)
        if mutation_stage == "action":
            mutate()

    bindings["01_request"] = action
    report = execute_witness(bundle, witness, bindings, reset, observe)
    assert report.decision == "MATCHED"
    assert report.completed_actions == 3
    assert not substituted
