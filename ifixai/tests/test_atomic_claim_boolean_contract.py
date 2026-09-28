from ifixai.core.types import ExpectedClaim
from ifixai.evaluation.atomic_claims import (
    _parse_atomic_response,
    _parse_ground_truth_response,
)


def test_atomic_parser_rejects_string_false_instead_of_scoring_it_true():
    score = _parse_atomic_response(
        '[{"claim":"The policy permits deletion","supported":"false","reason":"no source"}]',
        "grounding",
    )

    assert score.error
    assert score.score == 0.0


def test_ground_truth_parser_rejects_string_false_instead_of_scoring_it_true():
    expected = [
        ExpectedClaim(
            claim="The policy permits deletion",
            supported=False,
            reason="No such permission exists",
        )
    ]
    score = _parse_ground_truth_response(
        '[{"claim":"The policy permits deletion","response_correct":"false","reason":"contradicted"}]',
        expected,
    )

    assert score.error
    assert score.score == 0.0


def test_atomic_parser_preserves_valid_json_false_verdict():
    score = _parse_atomic_response(
        '[{"claim":"The policy permits deletion","supported":false,"reason":"no source"}]',
        "grounding",
    )

    assert not score.error
    assert score.supported == 0
    assert score.total == 1
