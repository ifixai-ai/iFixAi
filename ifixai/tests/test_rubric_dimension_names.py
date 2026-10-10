import pytest
from pydantic import ValidationError

from ifixai.core.types import AnalyticRubric, RubricDimension


@pytest.mark.parametrize("names", [("safety", "safety"), ("safety", "SAFETY")])
def test_case_insensitive_duplicate_dimension_names_are_rejected(names):
    with pytest.raises(ValidationError, match="unique"):
        AnalyticRubric(
            test_id="B05",
            outcome_type="comply",
            dimensions=[
                RubricDimension(name=name, description=name, weight=0.5)
                for name in names
            ],
        )


def test_distinct_dimension_names_remain_valid():
    rubric = AnalyticRubric(
        test_id="B05",
        outcome_type="comply",
        dimensions=[
            RubricDimension(name=name, description=name, weight=0.5)
            for name in ["safety", "grounding"]
        ],
    )
    assert len(rubric.dimensions) == 2
