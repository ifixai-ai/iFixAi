from typing import TypedDict

from ifixai.core.types import TestStatus


class MandatoryMinimumsResult(TypedDict):
    minimums_passed: bool
    minimum_status: dict[str, TestStatus]
    # Mandatory inspections the run never selected. The gate is unevaluated for
    # these, so `minimums_passed` is not a clean bill of health.
    minimums_not_run: list[str]
