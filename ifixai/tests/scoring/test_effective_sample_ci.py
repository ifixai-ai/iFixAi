import pytest

from ifixai.core.types import EvidenceItem
from ifixai.evaluation.proportion_ci import ProportionCI, wilson_interval
from ifixai.scoring.engine import compute_test_ci


def _item(case_id: str, passed: bool, n_effective: int) -> EvidenceItem:
    return EvidenceItem(
        test_case_id=case_id,
        passed=passed,
        details={"n_effective": n_effective},
    )


@pytest.mark.parametrize(
    ("evidence", "passed", "total"),
    [
        ([_item("measurement", True, 1), _item("diagnostic", False, 0)], 1, 1),
        ([_item("passed", True, 1), _item("failed", False, 3)], 1, 4),
    ],
)
def test_ci_uses_effective_weights_for_both_successes_and_sample_size(
    evidence: list[EvidenceItem], passed: int, total: int
) -> None:
    ci = compute_test_ci(evidence)
    expected = wilson_interval(passed, total)

    assert ci is not None
    assert ci.sample_size == total
    assert ci.lower == pytest.approx(expected["lower"], abs=0.0001)
    assert ci.upper == pytest.approx(expected["upper"], abs=0.0001)


def test_explicit_override_without_weights_keeps_empirical_rate() -> None:
    evidence = [
        EvidenceItem(test_case_id="passed", passed=True),
        EvidenceItem(test_case_id="failed", passed=False),
    ]
    ci = ProportionCI().compute(evidence, n_effective_override=4)
    expected = wilson_interval(2, 4)

    assert ci.lower == pytest.approx(expected["lower"], abs=0.0001)
    assert ci.upper == pytest.approx(expected["upper"], abs=0.0001)
