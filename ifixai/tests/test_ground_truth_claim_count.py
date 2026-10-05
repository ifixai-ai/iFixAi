from ifixai.core.types import ExpectedClaim
from ifixai.evaluation.atomic_claims import _parse_ground_truth_response


def _expected_claims():
    return [
        ExpectedClaim(claim="Claim one", supported=False, reason="Unsupported"),
        ExpectedClaim(claim="Claim two", supported=False, reason="Unsupported"),
    ]


def test_ground_truth_rejects_extra_judge_verdicts():
    score = _parse_ground_truth_response(
        '[{"claim":"Claim one","response_correct":true,"reason":"ok"},'
        '{"claim":"Claim two","response_correct":true,"reason":"ok"},'
        '{"claim":"Extra claim","response_correct":true,"reason":"ok"}]',
        _expected_claims(),
    )

    assert score.error
    assert score.score == 0.0


def test_ground_truth_rejects_missing_judge_verdicts():
    score = _parse_ground_truth_response(
        '[{"claim":"Claim one","response_correct":true,"reason":"ok"}]',
        _expected_claims(),
    )

    assert score.error
    assert score.score == 0.0


def test_ground_truth_accepts_one_verdict_per_expected_claim():
    score = _parse_ground_truth_response(
        '[{"claim":"Claim one","response_correct":true,"reason":"ok"},'
        '{"claim":"Claim two","response_correct":false,"reason":"wrong"}]',
        _expected_claims(),
    )

    assert not score.error
    assert score.total == 2
    assert score.supported == 1
    assert score.score == 0.5
