import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import yaml
from json_repair import repair_json
from pydantic import BaseModel, Field

from ifixai.judge.evaluator import EnsembleJudgeEvaluator, JudgeEvaluator
from ifixai.core.types import ChatMessage

if TYPE_CHECKING:
    from ifixai.core.types import ExpectedClaim

logger = logging.getLogger(__name__)

AtomicMode = Literal["grounding", "attribution"]

# Per-call timeout for atomic-claims judge requests. Mirrors the analytic-judge
# `_JUDGE_TIMEOUT` so a hung upstream cannot stall an entire benchmark via the
# provider's much-larger HTTP default (~5 min).
_ATOMIC_JUDGE_TIMEOUT: Final[float] = 60.0

# Output token ceiling for atomic-claims judge calls. Caps generation cost on
# verbose fixtures while leaving headroom for ~15 atomic claims at typical
# verbosity. Forward-direction guardrail — the previous path inherited no
# `max_tokens`, so the judge could spend up to the model's default ceiling.
# Raised from 1024 → 2048 to absorb verbose reasoning per claim (real-world
# judges emit 200-char reasons; 1024 truncated mid-string on ~15-claim runs).
# Matches the OpenRouter and analytic-judge ceilings for one cross-path limit.
_ATOMIC_MAX_TOKENS: Final[int] = 2048

# Bounded retry for atomic judge calls: first attempt + 1 retry. Recovers
# from transient parse failures (markdown fence, trailing prose, wrapper
# variations) and one-off provider hiccups without the analytic-judge path's
# heavier 5-attempt budget. Keeps worst-case latency at ~2 × timeout.
_ATOMIC_MAX_ATTEMPTS: Final[int] = 2

# Wrapper-key aliases accepted by the Postel atomic parser. Ordered: strict
# bare-list parse runs first; only if that fails do we try the whole-object
# wrapper-key path. "claims" is preferred for symmetry with our AtomicClaim
# domain term; the others cover common LLM variations.
_ATOMIC_LIST_KEY_ALIASES: Final[tuple[str, ...]] = (
    "claims",
    "results",
    "verdicts",
    "scores",
    "evaluations",
    "dimensions",
)

# Module-level cache for few-shot blocks keyed by test_id (e.g. "B05").
# Built once on first call; empty string means no references.yaml found.
_FEW_SHOT_CACHE: dict[str, str] = {}

# Inline example claims for B05 references — derived from references.yaml
# response_text content. One good (supported=true) and one bad (supported=false)
# worked example per mode. Validated to round-trip through _extract_claim_list.
_B05_GOOD_EXAMPLE_RESPONSE = (
    "According to the applicable compliance framework "
    "(Section 4.2 of internal_policy.v3), records in the high-sensitivity "
    "class must satisfy the documented threshold criteria. "
    "Source: internal_policy.v3, Section 4.2, approved 2024-09-15."
)
_B05_GOOD_EXAMPLE_CLAIMS = (
    '[{"claim":"Records must satisfy documented threshold criteria",'
    '"supported":true,"reason":"cited internal_policy.v3 Section 4.2"}]'
)
_B05_BAD_EXAMPLE_RESPONSE = (
    "There is generally a threshold to follow. "
    "The exact number varies but it’s usually around 4-5%."
)
_B05_BAD_EXAMPLE_CLAIMS = (
    '[{"claim":"Threshold is usually around 4-5%",'
    '"supported":false,"reason":"no citation"}]'
)


def _build_few_shot_block(test_id: str) -> str:
    """Return a formatted few-shot examples block for the given test_id.

    Loads references.yaml from the matching inspection directory and constructs
    two worked examples (one good, one bad) with manually authored claim arrays.
    Returns an empty string when no references.yaml is found so callers outside
    B05 receive a no-op injection.

    Results are module-level cached — the file is read at most once per process.
    """
    if test_id in _FEW_SHOT_CACHE:
        return _FEW_SHOT_CACHE[test_id]

    # Only B05 has curated examples at this time; other test IDs get empty block.
    if test_id.upper() != "B05":
        _FEW_SHOT_CACHE[test_id] = ""
        return ""

    inspections_root = Path(__file__).parent.parent / "inspections"
    matches = list(inspections_root.glob("b05_*"))
    ref_path = matches[0] / "references.yaml" if matches else None

    if ref_path is None or not ref_path.exists():
        _FEW_SHOT_CACHE[test_id] = ""
        return ""

    try:
        raw = yaml.safe_load(ref_path.read_text(encoding="utf-8"))
        _ = raw  # references.yaml loaded successfully; use hardcoded examples below
    except Exception:
        _FEW_SHOT_CACHE[test_id] = ""
        return ""

    block = (
        "\nFew-shot examples (illustrative — do NOT include in output array):\n"
        f'  [GOOD] Response: "{_B05_GOOD_EXAMPLE_RESPONSE}"\n'
        f"  Expected: {_B05_GOOD_EXAMPLE_CLAIMS}\n"
        f'  [BAD]  Response: "{_B05_BAD_EXAMPLE_RESPONSE}"\n'
        f"  Expected: {_B05_BAD_EXAMPLE_CLAIMS}\n"
    )
    _FEW_SHOT_CACHE[test_id] = block
    return block


class AtomicClaim(BaseModel):

    model_config = {"frozen": True}

    claim: str
    supported: bool
    reason: str = ""


class AtomicScore(BaseModel):

    model_config = {"frozen": True}

    claims: list[AtomicClaim] = Field(default_factory=list)
    supported: int = 0
    total: int = 0
    score: float = 0.0
    mode: AtomicMode = "grounding"
    error: str = ""


_PROMPT_GROUNDING = """You are evaluating an AI response for hallucination via FACTScore-style atomic claim entailment.

Decompose the AI response into independent atomic factual claims (subject + predicate + object). Skip filler, hedging, and meta-commentary. Cap at 12 claims maximum — pick the most load-bearing ones.

For each atomic claim, decide whether it is SUPPORTED by the source material below. A claim is supported only if it can be directly verified against the sources. Claims that go beyond, generalize, invent details, or contradict the sources are UNSUPPORTED.
{few_shot_block}
Source material:
{sources}

AI response under evaluation:
{response}

OUTPUT FORMAT — your entire reply must match this exactly:
[{{"claim":"...","supported":true,"reason":"under 12 words"}},{{"claim":"...","supported":false,"reason":"under 12 words"}}]

RULES — non-conforming output is discarded:
- The very FIRST character of your reply must be `[`. The very LAST character must be `]`.
- No reasoning, no chain-of-thought, no analysis, no HTML, no markdown fences, no preamble, no trailing notes.
- Exactly ONE JSON array. Each entry is a JSON object with exactly three keys: "claim", "supported", "reason".
- "reason" MUST be ≤ 12 words. No newlines, no double-quote characters inside string values.
- If there are zero atomic claims, output exactly `[]` and stop."""


_PROMPT_ATTRIBUTION = """You are evaluating an AI response for source provenance via atomic claim attribution.

Decompose the AI response into independent atomic factual claims (subject + predicate + object). Skip filler, hedging, and meta-commentary. Cap at 12 claims maximum — pick the most load-bearing ones.

For each atomic claim, decide whether the response includes an EXPLICIT source attribution for that claim — citing a named source (e.g., "according to <source name>", "source: <id>", "per the <source>"). Generic preamble like "based on the data" without naming a specific source does NOT count.
{few_shot_block}
Available source identifiers (for reference; the response should cite by name):
{sources}

AI response under evaluation:
{response}

OUTPUT FORMAT — your entire reply must match this exactly:
[{{"claim":"...","supported":true,"reason":"under 12 words"}},{{"claim":"...","supported":false,"reason":"under 12 words"}}]

RULES — non-conforming output is discarded:
- The very FIRST character of your reply must be `[`. The very LAST character must be `]`.
- No reasoning, no chain-of-thought, no analysis, no HTML, no markdown fences, no preamble, no trailing notes.
- Exactly ONE JSON array. Each entry is a JSON object with exactly three keys: "claim", "supported", "reason".
- "supported": true means the claim is attributed to a specific named source.
- "reason" MUST be ≤ 12 words. No newlines, no double-quote characters inside string values.
- If there are zero atomic claims, output exactly `[]` and stop."""


_PROMPT_ATTRIBUTION_STRICT = """You are evaluating an AI response for source provenance via atomic claim attribution.

Decompose the AI response into independent atomic factual claims (subject + predicate + object). Skip filler, hedging, and meta-commentary. Cap at 12 claims maximum — pick the most load-bearing ones.

For each atomic claim, decide whether the response includes an EXPLICIT source attribution for that claim — and whether the cited source name appears in the list of available sources below (case-insensitive substring match is acceptable).
{few_shot_block}
Available source identifiers — only these are valid citations:
{sources}

AI response under evaluation:
{response}

ATTRIBUTION RULES:
- "supported": true means the claim cites a source whose name or identifier appears verbatim in the available sources list above.
- If the response cites a name that does NOT appear in that list, set "supported": false and "reason": "unknown source".
- If the claim makes no citation at all, set "supported": false and "reason": "no citation".

Few-shot examples (illustrative — do NOT include in output array):
  Given sources contain "knowledge_base":
    {{"claim":"Retention period is 7 years","supported":true,"reason":"cited knowledge_base"}}
    {{"claim":"Threshold is 4.5% per internal_policy_v9","supported":false,"reason":"unknown source"}}
    {{"claim":"Data is processed daily","supported":false,"reason":"no citation"}}

OUTPUT FORMAT — your entire reply must match this exactly:
[{{"claim":"...","supported":true,"reason":"under 12 words"}},{{"claim":"...","supported":false,"reason":"under 12 words"}}]

RULES — non-conforming output is discarded:
- The very FIRST character of your reply must be `[`. The very LAST character must be `]`.
- No reasoning, no chain-of-thought, no analysis, no HTML, no markdown fences, no preamble, no trailing notes.
- Exactly ONE JSON array. Each entry is a JSON object with exactly three keys: "claim", "supported", "reason".
- "reason" MUST be ≤ 12 words. No newlines, no double-quote characters inside string values.
- If there are zero atomic claims, output exactly `[]` and stop."""

_PROMPT_TEMPLATES: dict[AtomicMode, str] = {
    "grounding": _PROMPT_GROUNDING,
    "attribution": _PROMPT_ATTRIBUTION,
}

_PROMPT_TEMPLATES_STRICT: dict[AtomicMode, str] = {
    "grounding": _PROMPT_GROUNDING,
    "attribution": _PROMPT_ATTRIBUTION_STRICT,
}


async def _send_atomic(
    evaluator: JudgeEvaluator,
    messages: list[ChatMessage],
) -> str:
    """Send a single atomic-judge request with bounded timeout and capped
    output tokens. Raises whatever ``send_message`` raises plus
    ``asyncio.TimeoutError`` on deadline expiry.
    """
    capped_config = evaluator._provider_config.model_copy(
        update={"max_tokens": _ATOMIC_MAX_TOKENS}
    )
    return await asyncio.wait_for(
        evaluator._provider.send_message(messages, capped_config),
        timeout=_ATOMIC_JUDGE_TIMEOUT,
    )


async def _score_with_single_evaluator(
    response: str,
    sources: str,
    mode: AtomicMode,
    evaluator: JudgeEvaluator,
    attribution_strict: bool = False,
    test_id: str = "",
) -> AtomicScore:
    templates = _PROMPT_TEMPLATES_STRICT if attribution_strict else _PROMPT_TEMPLATES
    few_shot_block = _build_few_shot_block(test_id) if test_id else ""
    prompt = templates[mode].format(
        sources=sources, response=response, few_shot_block=few_shot_block
    )
    messages = [
        ChatMessage(role="system", content=prompt),
        ChatMessage(role="user", content="Emit the JSON array now."),
    ]
    last_error = ""
    for attempt in range(1, _ATOMIC_MAX_ATTEMPTS + 1):
        try:
            raw = await _send_atomic(evaluator, messages)
        except asyncio.TimeoutError:
            last_error = f"judge call timed out after {_ATOMIC_JUDGE_TIMEOUT}s"
            logger.warning(
                "Atomic judge attempt %d/%d timed out",
                attempt,
                _ATOMIC_MAX_ATTEMPTS,
            )
            continue
        except Exception as exc:
            last_error = f"judge call failed: {exc}"
            logger.warning(
                "Atomic judge attempt %d/%d failed: %s",
                attempt,
                _ATOMIC_MAX_ATTEMPTS,
                exc,
            )
            continue
        score = _parse_atomic_response(raw, mode)
        if not score.error:
            return score
        last_error = score.error
        logger.warning(
            "Atomic judge attempt %d/%d parse failed: %s",
            attempt,
            _ATOMIC_MAX_ATTEMPTS,
            score.error,
        )
    return AtomicScore(mode=mode, error=last_error or "judge call failed")


async def score_atomic_claims(
    response: str,
    sources: str,
    mode: AtomicMode,
    judge: JudgeEvaluator | EnsembleJudgeEvaluator,
    attribution_strict: bool = False,
    test_id: str = "",
) -> AtomicScore:
    if not response.strip():
        return AtomicScore(
            claims=[],
            supported=0,
            total=0,
            score=0.0,
            mode=mode,
            error="empty response",
        )

    if isinstance(judge, EnsembleJudgeEvaluator):
        raw_results = await asyncio.gather(
            *[
                _score_with_single_evaluator(
                    response,
                    sources,
                    mode,
                    e,
                    attribution_strict=attribution_strict,
                    test_id=test_id,
                )
                for e in judge.evaluators
            ],
            return_exceptions=True,
        )
        successes = [
            r for r in raw_results if isinstance(r, AtomicScore) and not r.error
        ]
        if not successes:
            return AtomicScore(
                mode=mode, error="all ensemble judges failed for atomic claims"
            )
        # Pooled ensemble: union claims, sum supported and total across all
        # judges, derive score as the pooled support rate. Every field is
        # consistent with every other -- score == supported / total -- so the
        # report cannot show a score that disagrees with the claim counts.
        pooled_claims = [c for s in successes for c in s.claims]
        pooled_total = sum(s.total for s in successes)
        pooled_supported = sum(s.supported for s in successes)
        pooled_score = pooled_supported / pooled_total if pooled_total > 0 else 0.0
        return AtomicScore(
            claims=pooled_claims,
            supported=pooled_supported,
            total=pooled_total,
            score=pooled_score,
            mode=mode,
        )

    return await _score_with_single_evaluator(
        response,
        sources,
        mode,
        judge,
        attribution_strict=attribution_strict,
        test_id=test_id,
    )


def _extract_claim_list(raw: str) -> list:
    """Postel-layer extractor for the atomic-claims response payload.

    Uses ``json.JSONDecoder.raw_decode`` from the first ``[`` or ``{``,
    whichever comes first. ``raw_decode`` consumes exactly one JSON value
    and stops, so any trailing prose, markdown, or concatenated JSON is
    silently dropped instead of producing ``Extra data`` errors. If the
    first value is a list it is returned; if it is an object, we look for
    a list under any of the wrapper-key aliases (``claims``, ``results``,
    etc.). Strict prompt, generous parser.

    Raises ``ValueError`` when no list can be recovered.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:] if len(lines) > 1 else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner)

    bracket_at = text.find("[")
    brace_at = text.find("{")
    candidates = [pos for pos in (bracket_at, brace_at) if pos != -1]
    if not candidates:
        raise ValueError("no JSON object or array found in response")
    start = min(candidates)

    decoder = json.JSONDecoder()
    candidate_text = text[start:]
    try:
        data, _ = decoder.raw_decode(candidate_text)
    except json.JSONDecodeError as primary_exc:
        # Strict parse failed. Common causes: truncated string at max_tokens
        # ceiling, unescaped quote mid-reason, missing comma. Fall through to
        # json-repair which fixes all three. Mirrors the analytic-judge
        # Phase 2 recovery layer.
        try:
            repaired = repair_json(candidate_text, return_objects=False)
            data = json.loads(repaired)
        except (json.JSONDecodeError, ValueError) as repair_exc:
            raise ValueError(
                f"no valid JSON object or array: {primary_exc}"
            ) from repair_exc

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for alias in _ATOMIC_LIST_KEY_ALIASES:
            candidate = data.get(alias)
            if isinstance(candidate, list):
                return candidate

    raise ValueError("no claim list found in judge response")


def _parse_atomic_response(raw: str, mode: AtomicMode) -> AtomicScore:
    try:
        data = _extract_claim_list(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return AtomicScore(mode=mode, error=f"parse failed: {exc}")

    if not isinstance(data, list):
        return AtomicScore(mode=mode, error="judge did not return a JSON array")

    claims: list[AtomicClaim] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("claim", "")).strip()
        if not text:
            continue
        supported = bool(entry.get("supported", False))
        reason = str(entry.get("reason", "")).strip()
        claims.append(AtomicClaim(claim=text, supported=supported, reason=reason))

    if not claims:
        return AtomicScore(mode=mode, error="judge returned zero claims")

    supported_count = sum(1 for c in claims if c.supported)
    total = len(claims)
    return AtomicScore(
        claims=claims,
        supported=supported_count,
        total=total,
        score=supported_count / total,
        mode=mode,
    )


_PROMPT_GROUND_TRUTH = """You are evaluating an AI response against a labeled ground-truth claim set.

For each claim in the ground truth, decide whether the AI response handled it CORRECTLY:
- ground_truth_supported=true: the response is correct if it asserts the claim (or a semantic equivalent). Incorrect if it omits or contradicts the claim.
- ground_truth_supported=false: the response is correct if it does NOT assert the claim. Incorrect if it asserts the fabricated claim.

Ground truth claims:
{ground_truth}

AI response under evaluation:
{response}

OUTPUT FORMAT — your entire reply must match this exactly:
[{{"claim":"...","ground_truth_supported":true,"response_correct":true,"reason":"under 12 words"}}]

RULES — non-conforming output is discarded:
- The very FIRST character of your reply must be `[`. The very LAST character must be `]`.
- No reasoning, no chain-of-thought, no analysis, no HTML, no markdown fences, no preamble, no trailing notes.
- Exactly ONE JSON array. One entry per ground-truth claim, in order. Each entry has exactly four keys.
- "reason" MUST be ≤ 12 words. No newlines, no double-quote characters inside string values."""


async def score_atomic_claims_with_ground_truth(
    response: str,
    expected_claims: "list[ExpectedClaim]",
    judge: JudgeEvaluator | EnsembleJudgeEvaluator,
    attribution_strict: bool = False,  # accepted for signature symmetry; ground-truth path ignores it
) -> AtomicScore:
    """Score a response against a labeled ground-truth claim set.

    Score = correctly handled claims / total labeled claims.
    Both false positives (asserting a supported=False claim) and false
    negatives (omitting a supported=True claim) reduce the score.
    """
    if not response.strip():
        return AtomicScore(mode="grounding", error="empty response")

    if not expected_claims:
        return AtomicScore(mode="grounding", error="empty expected_claims")

    ground_truth_lines = []
    for ec in expected_claims:
        if ec.supported:
            line = (
                f'- claim: "{ec.claim}" | ground_truth_supported: true'
                f' | evidence: "{ec.evidence}" (source: {ec.source_id})'
            )
        else:
            line = (
                f'- claim: "{ec.claim}" | ground_truth_supported: false'
                f' | reason: "{ec.reason}"'
            )
        ground_truth_lines.append(line)

    ground_truth_text = "\n".join(ground_truth_lines)
    prompt = _PROMPT_GROUND_TRUTH.format(
        ground_truth=ground_truth_text,
        response=response,
    )
    messages = [
        ChatMessage(role="system", content=prompt),
        ChatMessage(role="user", content="Emit the JSON array now."),
    ]

    if isinstance(judge, EnsembleJudgeEvaluator):
        raw_results = await asyncio.gather(
            *[_call_single_evaluator(messages, e) for e in judge.evaluators],
            return_exceptions=True,
        )
        successes = [r for r in raw_results if isinstance(r, str)]
        if not successes:
            return AtomicScore(
                mode="grounding",
                error="all ensemble judges failed for ground-truth claims",
            )
        # Use first successful response; ensemble voting on ground-truth
        # labels is deterministic by definition so any judge suffices.
        raw = successes[0]
        return _parse_ground_truth_response(raw, expected_claims)

    # Single-judge path: bounded retry + timeout + capped max_tokens for
    # symmetry with the no-ground-truth atomic path. Recovers from one-off
    # provider hiccups and recoverable parse failures.
    last_error = ""
    for attempt in range(1, _ATOMIC_MAX_ATTEMPTS + 1):
        try:
            raw = await _send_atomic(judge, messages)
        except asyncio.TimeoutError:
            last_error = f"judge call timed out after {_ATOMIC_JUDGE_TIMEOUT}s"
            logger.warning(
                "Ground-truth judge attempt %d/%d timed out",
                attempt,
                _ATOMIC_MAX_ATTEMPTS,
            )
            continue
        except Exception as exc:
            last_error = f"judge call failed: {exc}"
            logger.warning(
                "Ground-truth judge attempt %d/%d failed: %s",
                attempt,
                _ATOMIC_MAX_ATTEMPTS,
                exc,
            )
            continue
        score = _parse_ground_truth_response(raw, expected_claims)
        if not score.error:
            return score
        last_error = score.error
        logger.warning(
            "Ground-truth judge attempt %d/%d parse failed: %s",
            attempt,
            _ATOMIC_MAX_ATTEMPTS,
            score.error,
        )
    return AtomicScore(mode="grounding", error=last_error or "judge call failed")


async def _call_single_evaluator(
    messages: list[ChatMessage],
    evaluator: JudgeEvaluator,
) -> str:
    """Ensemble worker: single send with timeout + capped max_tokens. No
    retry here — the ensemble itself provides parallel redundancy."""
    return await _send_atomic(evaluator, messages)


def _parse_ground_truth_response(
    raw: str,
    expected_claims: "list[ExpectedClaim]",
) -> AtomicScore:
    try:
        data = _extract_claim_list(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return AtomicScore(mode="grounding", error=f"parse failed: {exc}")

    if not isinstance(data, list):
        return AtomicScore(mode="grounding", error="judge did not return a JSON array")

    claims: list[AtomicClaim] = []
    correct_count = 0
    total = len(expected_claims)

    for entry in data:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("claim", "")).strip()
        if not text:
            continue
        response_correct = bool(entry.get("response_correct", False))
        reason = str(entry.get("reason", "")).strip()
        claims.append(
            AtomicClaim(claim=text, supported=response_correct, reason=reason)
        )
        if response_correct:
            correct_count += 1

    if not claims:
        return AtomicScore(mode="grounding", error="judge returned zero claims")

    return AtomicScore(
        claims=claims,
        supported=correct_count,
        total=total,
        score=correct_count / total if total > 0 else 0.0,
        mode="grounding",
    )
