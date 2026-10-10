import json

import pytest

from ifixai.evaluation.atomic_claims import _parse_atomic_response


@pytest.mark.parametrize("duplicate", ["The limit is 5.", " the LIMIT is   5. "])
def test_duplicate_atomic_claims_are_rejected(duplicate):
    raw = json.dumps(
        [
            {"claim": "The limit is 5.", "supported": True},
            {"claim": duplicate, "supported": True},
            {"claim": "The owner is Pat.", "supported": False},
        ]
    )
    assert "duplicate" in _parse_atomic_response(raw, "grounding").error


def test_distinct_claims_retain_fractional_score():
    raw = json.dumps(
        [
            {"claim": "The limit is 5.", "supported": True},
            {"claim": "The owner is Pat.", "supported": False},
        ]
    )
    score = _parse_atomic_response(raw, "grounding")
    assert not score.error
    assert score.score == 0.5
