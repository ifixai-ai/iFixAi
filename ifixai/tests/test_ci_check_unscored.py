import asyncio

import pytest

from ifixai.core.types import TestGrade, TestRunResult
from ifixai.examples import ci_check


def _config() -> dict[str, str]:
    return {
        "provider": "mock",
        "api_key": "test-key",
        "fixture": "test-fixture",
        "model": "",
        "system_name": "test-system",
        "system_version": "1.0",
        "min_score": "0.70",
        "min_strategic": "0.80",
        "report_path": "",
    }


@pytest.mark.parametrize(
    ("overall_score", "partial", "expected_message"),
    [
        (None, False, "Overall Score:  n/a"),
        (0.95, True, "partial run"),
    ],
)
def test_ci_check_fails_cleanly_when_run_is_not_gradeable(
    monkeypatch, capsys, overall_score, partial, expected_message
):
    async def fake_run_inspections(**kwargs):
        return TestRunResult(
            overall_score=overall_score,
            strategic_score=0.95,
            grade=TestGrade.A,
            mandatory_minimums_passed=True,
            partial=partial,
            abort_reason="judge quota exhausted" if partial else None,
        )

    monkeypatch.setattr(ci_check, "read_env_config", _config)
    monkeypatch.setattr(ci_check, "run_inspections", fake_run_inspections)

    assert asyncio.run(ci_check.run_ci_check()) == 1
    assert expected_message.lower() in capsys.readouterr().out.lower()


def test_ci_check_still_passes_a_complete_scored_run(monkeypatch):
    async def fake_run_inspections(**kwargs):
        return TestRunResult(
            overall_score=0.95,
            strategic_score=0.95,
            grade=TestGrade.A,
            mandatory_minimums_passed=True,
        )

    monkeypatch.setattr(ci_check, "read_env_config", _config)
    monkeypatch.setattr(ci_check, "run_inspections", fake_run_inspections)

    assert asyncio.run(ci_check.run_ci_check()) == 0
