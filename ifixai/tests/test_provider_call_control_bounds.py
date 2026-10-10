import pytest
from pydantic import ValidationError

from ifixai.core.types import ProviderConfig


@pytest.mark.parametrize(
    "controls",
    [
        {"max_retries": -1},
        {"timeout": 0},
        {"timeout": -1},
        {"max_tokens": 0},
        {"max_tokens": -1},
    ],
)
def test_invalid_call_controls_are_rejected(controls):
    with pytest.raises(ValidationError):
        ProviderConfig(provider="http", **controls)


def test_zero_retries_and_positive_limits_are_valid():
    config = ProviderConfig(provider="http", max_retries=0, timeout=1, max_tokens=1)
    assert config.max_retries == 0
    assert config.timeout == 1
    assert config.max_tokens == 1


def test_optional_token_limit_can_be_omitted():
    assert ProviderConfig(provider="http").max_tokens is None
