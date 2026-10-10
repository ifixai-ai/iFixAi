import pytest

from ifixai.evaluation.analytic_judge import _judge_timeout_from_env
from ifixai.evaluation.atomic_claims import _atomic_judge_timeout_from_env


@pytest.mark.parametrize(
    "parser", [_judge_timeout_from_env, _atomic_judge_timeout_from_env]
)
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_invalid_timeout_falls_back(parser, value, monkeypatch):
    monkeypatch.setenv("IFIXAI_JUDGE_TIMEOUT", value)
    assert parser(60.0) == 60.0


@pytest.mark.parametrize(
    "parser", [_judge_timeout_from_env, _atomic_judge_timeout_from_env]
)
def test_positive_timeout_is_preserved(parser, monkeypatch):
    monkeypatch.setenv("IFIXAI_JUDGE_TIMEOUT", "300.5")
    assert parser() == 300.5
