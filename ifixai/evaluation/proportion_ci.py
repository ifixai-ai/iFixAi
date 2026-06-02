from math import sqrt
from typing import TYPE_CHECKING

from ifixai.core.types import ConfidenceInterval
from ifixai.evaluation.schemas import WilsonInterval

if TYPE_CHECKING:
    from ifixai.core.types import EvidenceItem

_Z_95 = 1.959963984540054

def z_for_confidence(confidence_level: float) -> float:
    if confidence_level == 0.95:
        return _Z_95
    if confidence_level == 0.99:
        return 2.5758293035489004
    if confidence_level == 0.90:
        return 1.6448536269514722
    raise ValueError(
        f"Unsupported confidence_level={confidence_level}; "
        "supported values are 0.90, 0.95, 0.99"
    )

class ProportionCI:

    def __init__(self, confidence_level: float = 0.95) -> None:
        self._z = z_for_confidence(confidence_level)
        self._confidence_level = confidence_level

    def compute(
        self,
        evidence: "list[EvidenceItem]",
        n_effective_override: int | None = None,
    ) -> ConfidenceInterval:
        """Wilson CI over `evidence`.

        When `n_effective_override` is provided, treat that as the
        independent-sample count: the Wilson denominator and the reported
        `sample_size` use it instead of `len(evidence)`. The numerator
        becomes the count of canonical (deduped) items that passed,
        scaled by their share of the override. Use this when the raw
        evidence list contains correlated samples (e.g., 50 structurally
        identical items from a uniform provider).
        """
        if n_effective_override is not None:
            return self._compute_with_override(evidence, n_effective_override)

        sample_size = len(evidence)

        if sample_size == 0:
            return ConfidenceInterval(
                lower=0.0,
                upper=0.0,
                method="wilson",
                sample_size=0,
                warning="No evidence items to compute CI",
            )

        passed = sum(1 for e in evidence if e.passed)
        interval = wilson_interval(passed, sample_size, self._z)
        lower = interval["lower"]
        upper = interval["upper"]

        warning: str | None = None
        if sample_size < 5:
            warning = f"Small sample size (n={sample_size}); CI is wide"

        return ConfidenceInterval(
            lower=round(lower, 4),
            upper=round(upper, 4),
            method="wilson",
            sample_size=sample_size,
            warning=warning,
        )

    def _compute_with_override(
        self,
        evidence: "list[EvidenceItem]",
        n_effective: int,
    ) -> ConfidenceInterval:
        if n_effective <= 0:
            return ConfidenceInterval(
                lower=0.0,
                upper=0.0,
                method="wilson",
                sample_size=0,
                effective_sample_size=0,
                warning="n_effective_override <= 0; no signal",
            )

        # Numerator: empirical pass rate × n_effective. Round to the nearest
        # integer so wilson_interval receives an int "passed" count that
        # respects the effective sample size, not the inflated raw count.
        if evidence:
            pass_rate = sum(1 for e in evidence if e.passed) / len(evidence)
        else:
            pass_rate = 0.0
        passed_scaled = round(pass_rate * n_effective)
        passed_scaled = max(0, min(passed_scaled, n_effective))

        interval = wilson_interval(passed_scaled, n_effective, self._z)
        warning = (
            f"Effective sample size n_eff={n_effective} from {len(evidence)} "
            "raw items; CI reflects the deduped count, not the raw count."
        )
        return ConfidenceInterval(
            lower=round(interval["lower"], 4),
            upper=round(interval["upper"], 4),
            method="wilson",
            sample_size=n_effective,
            effective_sample_size=n_effective,
            warning=warning,
        )

def wilson_interval(passed: int, total: int, z: float = _Z_95) -> WilsonInterval:
    if total == 0:
        return WilsonInterval(lower=0.0, upper=0.0)
    if passed < 0 or passed > total:
        raise ValueError(
            f"Invalid proportion: passed={passed} total={total}"
        )
    p = passed / total
    denom = 1.0 + z * z / total
    centre = (p + z * z / (2.0 * total)) / denom
    half = (z * sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total))) / denom
    return WilsonInterval(lower=max(0.0, centre - half), upper=min(1.0, centre + half))
