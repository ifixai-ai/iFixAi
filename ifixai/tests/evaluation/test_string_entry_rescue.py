"""A judge's string-form dimension entry ("name: verdict") is read with every verdict word it may use."""

import pytest

from ifixai.evaluation.analytic_judge import _rescue_string_entry


@pytest.mark.parametrize(
    ("entry", "passed"),
    [
        ("no_leak: pass", True),
        ("no_leak: passed", True),
        ("no_leak - PASS", True),
        ("no_leak: fail", False),
        ("no_leak: failed", False),
        ("no_leak: Fail", False),
        ("no_leak: yes", True),
        ("no_leak: false", False),
    ],
)
def test_verdict_words_are_rescued(entry, passed):
    assert _rescue_string_entry(entry) == {"name": "no_leak", "passed": passed, "reasoning": ""}


@pytest.mark.parametrize("entry", ["no_leak: passes", "no_leak: failing", "no_leak: pas", "no_leak: passe"])
def test_other_words_are_not_rescued(entry):
    assert _rescue_string_entry(entry) is None
