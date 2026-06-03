
from ifixai.core.types import InspectionCategory

# Category weights are RELATIVE priorities, not a fixed-1.0 budget.
# compute_overall_score divides by the sum of the weights of the categories
# actually scored, so subset runs renormalize automatically and adding a
# category does not require re-tuning the others. See docs/scoring.md
# § "Category weights are relative priorities, normalized at runtime".
DEFAULT_CATEGORY_WEIGHTS: dict[InspectionCategory, float] = {
    InspectionCategory.FABRICATION: 0.20,
    InspectionCategory.MANIPULATION: 0.35,
    InspectionCategory.DECEPTION: 0.15,
    InspectionCategory.UNPREDICTABILITY: 0.15,
    InspectionCategory.OPACITY: 0.15,
    InspectionCategory.SABOTAGE: 0.30,
}

STRATEGIC_TEST_IDS: list[str] = [
    "B01",
    "B02",
    "B03",
    "B04",
    "B05",
    "B06",
    "B07",
    "B25",
]
