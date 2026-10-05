"""Registered diagnostics are summaries, never successful measurements."""

import pytest

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationPipelineConfig,
    EvidenceItem,
    JudgeErrorKind,
    ProviderConfig,
    TestResult,
    TestRunResult,
)
from ifixai.evaluation.pipeline import EvaluationPipeline
from ifixai.harness.registry import create_inspection
from ifixai.providers.base import ChatProvider
from ifixai.reporting.health import (
    judge_health_note,
    measurement_failure_banner,
    run_health,
)
from ifixai.shared.evidence import flag_diagnostics


class VerboseFixtureProvider(ChatProvider):
    """Owned canned responses exercise M12's real transcript-length guard."""

    def __init__(self):
        self.calls = 0

    async def send_message(self, messages, config):
        self.calls += 1
        return "Owned synthetic transcript content. " * 180


@pytest.mark.asyncio
async def test_registered_m12_diagnostics_cannot_hide_zero_measurements():
    fixture = load_fixture("healthcare")
    provider = VerboseFixtureProvider()
    config = EvaluationPipelineConfig()
    pipeline = EvaluationPipeline(config)
    inspection = create_inspection("M12")
    result = await inspection.execute(
        provider,
        ProviderConfig(provider="mock"),
        fixture,
        pipeline_config=config,
        pipeline=pipeline,
    )

    diagnostics = [item for item in result.evidence if item.is_diagnostic]
    measurements = [item for item in result.evidence if not item.is_diagnostic]
    assert len(diagnostics) == 3
    assert len(measurements) == 40
    assert all(item.extraction_error == JudgeErrorKind.CONTRACT for item in measurements)
    assert provider.calls == 280
    assert pipeline.judge_calls_used == 0

    health = run_health(TestRunResult(test_results=[result]))
    assert health.total == 40
    assert health.scorable == 0
    assert health.invalid
    assert measurement_failure_banner(health) is not None


def _health(evidence):
    inspection = create_inspection("B01")
    return run_health(
        TestRunResult(
            test_results=[
                TestResult(
                    test_id=inspection.spec.test_id,
                    spec=inspection.spec,
                    evidence=evidence,
                )
            ]
        )
    )


def test_diagnostics_do_not_inflate_judge_attempts():
    health = _health(
        [
            EvidenceItem(test_case_id="measured", passed=True),
            EvidenceItem(
                test_case_id="broken", extraction_error=JudgeErrorKind.CONTRACT
            ),
            EvidenceItem(test_case_id="summary", is_diagnostic=True, passed=True),
        ]
    )
    assert health.total == 2
    assert health.scorable == 1
    assert health.judge_broke == 1
    assert health.judge_attempts == 2
    assert "of 2 grading attempts" in judge_health_note(health)


def test_unflagged_evidence_keeps_existing_measurement_accounting():
    health = _health(
        [
            EvidenceItem(test_case_id="one", passed=True),
            EvidenceItem(test_case_id="two", passed=False),
        ]
    )
    assert health.total == health.scorable == 2
    assert not health.invalid
    assert measurement_failure_banner(health) is None


def test_error_under_declared_diagnostic_prefix_remains_a_measurement():
    evidence = flag_diagnostics(
        [
            EvidenceItem(
                test_case_id="summary-contract",
                extraction_error=JudgeErrorKind.CONTRACT,
            )
        ],
        ("summary-",),
    )
    assert not evidence[0].is_diagnostic
    health = _health(evidence)
    assert health.total == health.judge_broke == 1
    assert health.invalid
