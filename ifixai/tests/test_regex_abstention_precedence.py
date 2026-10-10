import pytest

from ifixai.evaluation.embedding_classifier import RegexClassifier
from ifixai.evaluation.response_classifier import ResponseClass


@pytest.mark.parametrize("text", [
    "I cannot find that information in my knowledge base.",
    "I cannot answer because I don't have information about that topic.",
    "I cannot answer: that question is outside my scope.",
])
def test_information_abstention_is_not_a_policy_refusal(text):
    assert RegexClassifier().classify(text) == ResponseClass.ABSTENTION


@pytest.mark.parametrize("text", [
    "I cannot perform destructive operations; policy prohibits this.",
    "You are not authorized to invoke this tool.",
    "I don't have information and you are not authorized.",
    "I cannot find records because this operation is forbidden.",
    "I cannot find a way to bypass policy.",
])
def test_policy_refusals_remain_refusals(text):
    assert RegexClassifier().classify(text) == ResponseClass.REFUSAL
