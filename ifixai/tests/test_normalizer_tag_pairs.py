import pytest

from ifixai.evaluation.normalizer import normalize


@pytest.mark.parametrize("raw", [
    "<thinking>Access denied.</scratchpad>",
    "<reasoning>Access denied.</thinking>",
])
def test_mismatched_reasoning_tags_do_not_erase_the_response(raw):
    assert "Access denied." in normalize(raw).normalized


@pytest.mark.parametrize("tag", ["thinking", "reasoning", "scratchpad"])
def test_matching_reasoning_tags_are_removed(tag):
    assert normalize(f"<{tag}>private chain</{tag}>Final answer").normalized == "Final answer"
