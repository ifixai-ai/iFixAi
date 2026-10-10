import pytest
from pydantic import ValidationError

from ifixai.core.types import RubricDimension


@pytest.mark.parametrize(
    "weight", [-0.5, 1.5, float("nan"), float("inf"), float("-inf")]
)
def test_invalid_dimension_weight_is_rejected(weight):
    with pytest.raises(ValidationError):
        RubricDimension(name="safety", description="Safety", weight=weight)


@pytest.mark.parametrize("weight", [0.0, 0.25, 1.0])
def test_valid_dimension_weight_is_retained(weight):
    assert (
        RubricDimension(name="safety", description="Safety", weight=weight).weight
        == weight
    )
