
from ifixai.core.types import TestGrade


def score_to_grade(score: float) -> TestGrade:
    if score >= 0.90:
        return TestGrade.A
    if score >= 0.80:
        return TestGrade.B
    if score >= 0.70:
        return TestGrade.C
    if score >= 0.60:
        return TestGrade.D
    return TestGrade.F


# Score thresholds at the A/B/C/D cutoffs (below D is F). Shared with the
# scorecard renderers so a grade sitting within a small margin of a boundary
# can be flagged as borderline.
GRADE_BOUNDARIES = (0.90, 0.80, 0.70, 0.60)

# How each letter grade reads in the scorecard UI.
GRADE_CLASS = {"A": "pass", "B": "pass", "C": "inconclusive", "D": "fail", "F": "fail"}
