import random

from ifixai.harness.adversarial_mutator import _apply_synonym_substitute


def test_later_whole_word_is_used_after_an_embedded_prefix_match():
    text = "The undetectable record should be detectable."
    result = _apply_synonym_substitute(text, random.Random(1), (("detectable", "visible"),))
    assert result == "The undetectable record should be visible."


def test_embedded_word_alone_is_not_substituted():
    assert _apply_synonym_substitute("undetectable", random.Random(1), (("detectable", "visible"),)) == "undetectable"
