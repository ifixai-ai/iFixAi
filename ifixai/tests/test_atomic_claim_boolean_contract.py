import json

import pytest

from ifixai.core.types import ExpectedClaim
from ifixai.evaluation.atomic_claims import (
    _parse_atomic_response,
    _parse_ground_truth_response,
)


def test_atomic_parser_scores_string_false_as_false():
    score = _parse_atomic_response(
        '[{"claim":"The policy permits deletion","supported":"false","reason":"no source"}]',
        "grounding",
    )

    assert not score.error
    assert score.score == 0.0
    assert score.total == 1


def test_ground_truth_parser_scores_string_false_as_false():
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

    assert not score.error
    assert score.score == 0.0
    assert score.total == 1


def test_atomic_parser_preserves_valid_json_false_verdict():
    score = _parse_atomic_response(
        '[{"claim":"The policy permits deletion","supported":false,"reason":"no source"}]',
        "grounding",
    )

    assert not score.error
    assert score.supported == 0
    assert score.total == 1


@pytest.mark.parametrize(
    "verdict", [None, 0, 1, "yes", "no", "0", "1", "", [], {}, "false because"]
)
def test_parsers_reject_ambiguous_nonboolean_verdicts(verdict):
    atomic = _parse_atomic_response(
        json.dumps([{"claim": "policy", "supported": verdict}]), "grounding"
    )
    ground_truth = _parse_ground_truth_response(
        json.dumps([{"claim": "policy", "response_correct": verdict}]),
        [ExpectedClaim(claim="policy", supported=False, reason="unsupported")],
    )
    assert atomic.error
    assert atomic.score == 0.0
    assert ground_truth.error
    assert ground_truth.score == 0.0


@pytest.mark.parametrize(
    "verdict,expected_score",
    [(True, 1.0), (False, 0.0), (" TRUE ", 1.0), (" False ", 0.0)],
)
def test_parsers_accept_explicit_boolean_spellings(verdict, expected_score):
    atomic = _parse_atomic_response(
        json.dumps([{"claim": "policy", "supported": verdict}]), "grounding"
    )
    ground_truth = _parse_ground_truth_response(
        json.dumps([{"claim": "policy", "response_correct": verdict}]),
        [ExpectedClaim(claim="policy", supported=False, reason="unsupported")],
    )
    assert not atomic.error
    assert not ground_truth.error
    assert atomic.score == ground_truth.score == expected_score
