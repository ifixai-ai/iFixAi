"""Independent control: one empty cannot discard registered graded siblings."""
import pytest

from ifixai.tests.providers.test_http_empty_custom_runners_native import (
    execute_registered,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("test_id", ["V02", "V04", "V06", "V07", "V09"])
async def test_one_empty_keeps_registered_scored_siblings(test_id):
    reference, reference_calls = await execute_registered(test_id, None)
    actual, actual_calls = await execute_registered(test_id, "")
    expected = [item for item in reference.evidence
                if not item.is_diagnostic and item.extraction_error is None]
    observed = [item for item in actual.evidence
                if not item.is_diagnostic and item.extraction_error is None]
    assert expected, test_id
    assert len(observed) == len(expected), (test_id, len(observed), len(expected))
    assert actual.status == reference.status
    assert actual.score == reference.score
    assert actual_calls == reference_calls
    assert actual.error is None
