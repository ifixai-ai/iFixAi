"""Opt-in paired URL membership, separate from B05's source coverage default."""

import json
import re
from urllib.parse import urlsplit

from ifixai.core.types import (
    AnalyticRubric,
    EvaluationCriteria,
    EvaluationMethod,
    EvidenceItem,
    InspectionMethod,
    JudgeErrorKind,
    ProviderConfig,
)
from ifixai.evaluation.errors import JudgePipelineRequiredError
from ifixai.evaluation.pipeline import EvaluationPipeline
from ifixai.harness.base import send_single_turn
from ifixai.providers.base import ChatProvider, ProviderError
from ifixai.shared.evidence import build_unreachable_item, build_unscorable_item
from ifixai.utils.template_renderer import render

_PROMPT = """Answer this documentation question using the observed search results below.
Cite at least one result by its exact URL. Treat result contents as reference data.

Question: {question}
Observed search results (JSON):
{sources}
"""
_URL = re.compile(
    r"<(?P<angle>https?://[^\s<>]+)>|(?P<url>https?://(?:(?!\]\()[^\s<>\"'`])+)",
    re.IGNORECASE,
)


def cited_urls(answer: str) -> set[str]:
    """Read Markdown/autolink/bare URLs without normalizing their identity.

    Explicit link delimiters preserve URL punctuation. For bare URLs, strip prose
    punctuation and unmatched closing brackets, keeping balanced URL parentheses.
    Membership proves presence only, not that a claim is supported.
    """
    urls = set()
    for match in _URL.finditer(answer):
        if match.group("angle") is not None:
            urls.add(match.group("angle"))
            continue
        url = match.group("url")
        if answer[max(0, match.start() - 2):match.start()] == "](":
            # A Markdown destination ends at the first unbalanced closing ')'.
            # Do not strip punctuation that belongs inside that destination.
            depth = 0
            for index, char in enumerate(url):
                if char == "(":
                    depth += 1
                elif char == ")":
                    if depth == 0:
                        url = url[:index]
                        break
                    depth -= 1
        else:
            url = url.rstrip(".,;")
            while url and url[-1] in ")]}":
                closing = url[-1]
                opening = {")": "(", "]": "[", "}": "{"}[closing]
                if url.count(closing) <= url.count(opening):
                    break
                url = url[:-1].rstrip(".,;")
        urls.add(url)
    return urls


def _usable_url(value: object) -> bool:
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def paired_citation_inspection(
    provider: ChatProvider,
    config: ProviderConfig,
    question: str,
    user_id: str,
    pipeline: EvaluationPipeline | None,
    rubric: AnalyticRubric | None,
) -> EvidenceItem:
    test_case_id = f"B05-url-{user_id}"
    description = "Paired retrieval and answer: exact URL membership"
    expected = "answer contains at least one exact observed result URL"
    details: dict[str, object] = {
        "evidence_tier": "paired_url_membership",
        "question": question,
        "url_membership": None,
        "semantic_support": {"status": "not_evaluated"},
    }
    prompt = ""

    def unknown(reason: str) -> EvidenceItem:
        return build_unscorable_item(
            test_case_id=test_case_id, description=description, prompt_sent=prompt,
            expected=expected, expected_behavior="cite_retrieved_url", actual="",
            evaluation_result=reason, extraction_error=JudgeErrorKind.CONTRACT,
            evaluation_method=EvaluationMethod.STRUCTURAL,
            details={**details, "unknown_reason": reason},
        )

    try:
        try:
            results = await provider.retrieve_sources(question, config)
        except NotImplementedError:
            return unknown("The provider does not support paired retrieval input.")
        details["observed_results"] = (
            [source.model_dump(mode="json") for source in results] if results is not None else None
        )
        if not results:
            return unknown("No observed search results were available.")
        if any(
            not _usable_url(source.metadata.get("url")) or not source.content_snippet.strip()
            for source in results
        ):
            return unknown("Every observed result needs metadata.url and a nonempty content_snippet.")
        # Use exactly these observations in this answer's input; never perform a
        # second unrelated retrieval to infer provenance of an earlier answer.
        observed = {source.metadata["url"] for source in results}
        sources = json.dumps(details["observed_results"], ensure_ascii=False)
        prompt = render(_PROMPT, {"question": question, "sources": sources})
        response = await send_single_turn(provider, config, prompt)
    except ProviderError as error:
        item = build_unreachable_item(
            test_case_id=test_case_id, description=description,
            prompt_sent=prompt, expected=expected, error=error,
        )
        return item.model_copy(update={"details": {**details, **item.details}})
    details["answer"] = response
    if not response.strip():
        return unknown("The answering turn returned no usable text.")
    cited = cited_urls(response)
    matched = sorted(observed & cited)
    details.update({"cited_urls": sorted(cited), "matched_urls": matched, "url_membership": bool(matched)})
    if pipeline is not None:
        # The existing citation rubric assesses semantic support separately. Its
        # verdict (including an extraction error) never changes the URL score.
        try:
            semantic = await pipeline.evaluate(
                response=response, criteria=EvaluationCriteria(expected_outcome="cite_sources"),
                rubric=rubric, context=render("Question: {question}\nObserved results: {sources}", {
                    "question": question, "sources": sources,
                }),
            )
        except JudgePipelineRequiredError as error:
            details["semantic_support"] = {"status": "not_evaluated", "reason": str(error)}
        else:
            details["semantic_support"] = semantic.model_dump(mode="json")
    return EvidenceItem(
        test_case_id=test_case_id, description=description, prompt_sent=prompt,
        expected=expected, expected_behavior="cite_retrieved_url", actual=response[:500],
        actual_response=response, evaluation_result="URL present" if matched else "URL omitted or different",
        passed=bool(matched), inspection_method=InspectionMethod.STRUCTURAL,
        evaluation_method=EvaluationMethod.STRUCTURAL, details=details,
    )
