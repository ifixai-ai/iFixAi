"""An absent selected inspection cannot silently disappear from a measurement."""
import importlib

import pytest

from ifixai.core.types import EvidenceItem, TestResult, TestRunResult
from ifixai.windtunnel.defects import CLEAN

measurement = importlib.import_module("ifixai.windtunnel.measure")


@pytest.mark.parametrize("missing", ["clean", "defective", "both"])
async def test_missing_selected_result_refuses_measurement(monkeypatch, missing):
    calls = 0
    async def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        side = "clean" if calls == 1 else "defective"
        results = [] if missing in (side, "both") else [
            TestResult(test_id="B01", score=1.0, threshold=1.0,
                       evidence=[EvidenceItem(test_case_id="observed")])
        ]
        return TestRunResult(test_results=results, not_run_test_ids=[] if results else ["B01"])
    monkeypatch.setattr(measurement, "_run", run)
    with pytest.raises(ValueError, match=r"missing.*B01"):
        await measurement.measure(CLEAN, fixture="customer_support", test_ids={"B01"})


async def test_complete_paired_result_is_retained(monkeypatch):
    async def run(*args, **kwargs):
        return TestRunResult(test_results=[TestResult(
            test_id="B01", score=1.0, threshold=1.0,
            evidence=[EvidenceItem(test_case_id="observed")])])
    monkeypatch.setattr(measurement, "_run", run)
    report = await measurement.measure(CLEAN, fixture="customer_support", test_ids={"B01"})
    assert [outcome.test_id for outcome in report.outcomes] == ["B01"]


@pytest.mark.parametrize("side", ["clean", "defective"])
@pytest.mark.parametrize("problem", ["duplicate", "unexpected"])
async def test_ambiguous_live_results_are_rejected(monkeypatch, side, problem):
    calls = 0
    async def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        current_side = "clean" if calls == 1 else "defective"
        results = [TestResult(test_id="B01", score=1.0, threshold=1.0,
                              evidence=[EvidenceItem(test_case_id="observed")])]
        if current_side == side:
            results.append(results[0] if problem == "duplicate" else TestResult(test_id="B08"))
        return TestRunResult(test_results=results)
    monkeypatch.setattr(measurement, "_run", run)
    with pytest.raises(ValueError, match=problem):
        await measurement.measure(CLEAN, fixture="customer_support", test_ids={"B01"})
