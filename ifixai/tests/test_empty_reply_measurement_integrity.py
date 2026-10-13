"""Empty calls remain measurements without voiding actual structural grades."""

import pytest

from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import ProviderConfig, TestRunResult, TestStatus
from ifixai.harness.base import _InspectionResponses
from ifixai.inspections.b11_system_controllability.runner import (
    B11SystemControllability,
)
from ifixai.providers.base import (
    ProviderEmptyContentError,
    ProviderEmptyProbeError,
    ProviderResponseError,
)
from ifixai.providers.mock_governance import MockGovernanceProvider
from ifixai.reporting.health import measurement_failure_banner, run_health
from ifixai.tests.providers.test_http_empty_custom_runners_native import (
    execute_registered,
)


class EmptyGovernanceProvider(MockGovernanceProvider):
    """Real governance hooks with no usable conversational response."""

    async def send_message(self, messages, config):
        raise ProviderEmptyContentError(provider="mock", details="owned empty response")


@pytest.mark.asyncio
async def test_empty_error_identity_survives_response_observation():
    fixture = load_fixture("customer_support")
    observer = _InspectionResponses(EmptyGovernanceProvider(fixture.governance))
    with pytest.raises(ProviderEmptyProbeError) as caught:
        await observer.send_message([], ProviderConfig(provider="mock"))
    assert observer.attempts == observer.empty == 1
    assert observer.first_empty is caught.value.__cause__
    assert isinstance(caught.value, ProviderResponseError)
    assert not isinstance(caught.value, ProviderEmptyContentError)


@pytest.mark.asyncio
async def test_b11_preserves_five_actual_governance_results_when_chat_is_empty():
    fixture = load_fixture("customer_support")
    result = await B11SystemControllability().execute(
        EmptyGovernanceProvider(fixture.governance), ProviderConfig(provider="mock"), fixture,
    )
    scored = [item for item in result.evidence if item.extraction_error is None]
    assert len(scored) == 5
    assert all(item.passed for item in scored)
    assert result.status is TestStatus.PASS
    assert result.score == 1
    assert not result.insufficient_evidence
    assert any(item.extraction_error is not None for item in result.evidence)


@pytest.mark.asyncio
async def test_fully_empty_calls_remain_in_run_health_measurements():
    result, calls = await execute_registered("V01", "", all_empty=True)
    health = run_health(TestRunResult(test_results=[result]))
    assert result.status is TestStatus.INCONCLUSIVE
    measurements = [item for item in result.evidence if not item.is_diagnostic]
    assert health.total == len(measurements) == 40
    assert health.unreachable == health.total
    assert health.scorable == 0
    assert calls["judge"] == 0
    assert "RUN INVALID" in measurement_failure_banner(health)


@pytest.mark.asyncio
@pytest.mark.parametrize("test_id", ["V03", "V05", "V10"])
async def test_typed_empty_reply_keeps_existing_bounded_retry(test_id):
    healthy, healthy_calls = await execute_registered(test_id, "", empty_count=0)
    retry, retry_calls = await execute_registered(test_id, "", empty_count=1)
    healthy_scored = [item for item in healthy.evidence if item.extraction_error is None
                      and not item.is_diagnostic]
    retry_scored = [item for item in retry.evidence if item.extraction_error is None
                    and not item.is_diagnostic]
    assert len(retry_scored) == len(healthy_scored) > 0
    assert retry_calls["sut"] == healthy_calls["sut"] + 1
    assert retry_calls["judge"] == healthy_calls["judge"]
    assert retry.status == healthy.status
