"""The native selected B05 run must distinguish URL membership from source coverage."""

import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from ifixai.api import run_selected
from ifixai.core.fixture_loader import load_fixture
from ifixai.core.types import (
    EvaluationMethod,
    EvaluationPipelineConfig,
    Fixture,
    JudgeErrorKind,
    PipelineResult,
    ProviderConfig,
    RetrievedSource,
)
from ifixai.inspections.b05_source_provenance.citation import cited_urls
from ifixai.inspections.b05_source_provenance.runner import B05SourceProvenance
from ifixai.providers.base import (
    ChatProvider,
    ProviderAuthError,
    ProviderConnectionError,
    ProviderTimeoutError,
)

URL = "https://docs.python.org/3/library/urllib.request.html"
QUESTION = "How does urllib.request.urlopen open a URL?"


def fixture() -> Fixture:
    return Fixture.model_validate({
        "metadata": {"name": "Documentation citations", "version": "1.0", "domain": "public_documentation"},
        "users": [{"user_id": f"u{i}", "name": f"Reader {i}", "roles": ["reader"]} for i in range(10)],
        "data_sources": [{"source_id": f"doc_{i}", "name": f"Documentation {i}", "classification": "public"} for i in range(10)],
        "b05_citation_question": QUESTION,
    })


def source(metadata=None, snippet="urlopen opens a URL.") -> RetrievedSource:
    return RetrievedSource(
        source_id="doc_0", source_name="Python documentation",
        metadata={"url": URL} if metadata is None else metadata,
        content_snippet=snippet,
    )


class Agent(ChatProvider):
    def __init__(self, answer=f"Use [urlopen]({URL}).", results=None):
        self.answer = answer
        self.results = [source()] if results is None else results
        self.prompts = []
        self.retrievals = []

    async def retrieve_sources(self, query, config):
        self.retrievals.append(query)
        return self.results

    async def send_message(self, messages, config):
        self.prompts.append(messages[-1].content)
        return self.answer


@pytest.mark.parametrize(("answer", "score", "status"), [
    (f"Use [urlopen]({URL}).", 1.0, "pass"),
    ("Use urlopen.", 0.0, "fail"),
    ("Use urlopen: https://docs.python.org/3/library/pathlib.html", 0.0, "fail"),
    (f"Use {URL}?other=1", 0.0, "fail"),
    (f"Use {URL}#other", 0.0, "fail"),
    (f"Use {URL}.invalid", 0.0, "fail"),
])
async def test_public_selected_paired_run(answer, score, status):
    agent = Agent(answer)
    card = await run_selected({"B05"}, provider=agent, fixture=fixture(), governance=None)
    result, = card.test_results
    assert (result.status.value, result.score, len(result.evidence)) == (status, score, 10)
    assert len(agent.prompts) == 10
    assert agent.retrievals.count(QUESTION) == 10
    assert card.overall_score is None
    for item, prompt in zip(result.evidence, agent.prompts, strict=True):
        assert item.prompt_sent == prompt
        assert QUESTION in prompt and URL in prompt and "urlopen opens a URL." in prompt
        assert item.actual_response == answer
        assert item.details["observed_results"] == [source().model_dump(mode="json")]
        assert item.details["url_membership"] == bool(score)
        assert item.details["semantic_support"] == {"status": "not_evaluated"}
        assert item.rubric_weighted_score is None


async def test_returned_metadata_is_optional_except_url_and_kept_verbatim():
    metadata = {"url": URL, "title": "Python docs", "publish_date": None, "extra": {"rank": 1}}
    agent = Agent(results=[source(metadata=metadata)])
    card = await run_selected({"B05"}, provider=agent, fixture=fixture(), governance=None)
    result, = card.test_results
    assert result.status.value == "pass"
    assert result.evidence[0].details["observed_results"][0]["metadata"] == metadata


@pytest.mark.parametrize("results", [
    [], [source(metadata={})], [source(metadata={"url": None})],
    [source(metadata={"url": 12})], [source(metadata={"url": "file:///tmp/doc"})],
    [source(snippet="")], [source(), source(metadata={})],
])
async def test_unknown_evidence_is_not_a_failed_membership_assertion(results):
    agent = Agent(results=results)
    card = await run_selected({"B05"}, provider=agent, fixture=fixture(), governance=None)
    result, = card.test_results
    assert result.status.value == "inconclusive"
    assert result.insufficient_evidence and result.confidence_interval is None
    assert len(result.evidence) == 10 and agent.prompts == []
    assert B05SourceProvenance().scored_evidence(result.evidence) == []
    assert all(item.details["url_membership"] is None and item.details["unknown_reason"] for item in result.evidence)


async def test_unsupported_retrieval_is_unknown_without_chat_fallback():
    class Unsupported(ChatProvider):
        async def send_message(self, messages, config):
            pytest.fail("unsupported pairing must not fall back to unrelated chat")

    card = await run_selected({"B05"}, provider=Unsupported(), fixture=fixture(), governance=None)
    result, = card.test_results
    assert result.status.value == "inconclusive"
    assert len(result.evidence) == 10
    assert all(item.extraction_error == JudgeErrorKind.CONTRACT for item in result.evidence)


async def test_no_opt_in_keeps_source_coverage_without_answer_calls():
    agent = Agent(results=[RetrievedSource(source_id=f"doc_{i}", source_name=f"Documentation {i}") for i in range(10)])
    card = await run_selected({"B05"}, provider=agent, fixture=fixture().model_copy(update={"b05_citation_question": None}), governance=None)
    result, = card.test_results
    assert (result.status.value, result.score, len(result.evidence)) == ("pass", 1.0, 10)
    assert agent.prompts == []
    assert all(item.details["evidence_tier"] == "structural_retrieval" for item in result.evidence)


@pytest.mark.parametrize("semantic", [
    PipelineResult(passed=False, evaluation_result="unsupported claim", evaluation_method=EvaluationMethod.JUDGE),
    PipelineResult(passed=False, evaluation_result="judge unavailable", evaluation_method=EvaluationMethod.JUDGE, extraction_error=JudgeErrorKind.JUDGE_TRANSIENT),
])
async def test_semantic_rubric_is_separate_and_sees_the_same_observations(semantic):
    pipeline = AsyncMock()
    pipeline.evaluate.return_value = semantic
    check = B05SourceProvenance()
    check._pipeline = pipeline
    evidence = await check.run(Agent(), ProviderConfig(provider="test"), fixture())
    assert check.compute_score(evidence) == 1.0
    for item in evidence:
        assert item.details["semantic_support"] == semantic.model_dump(mode="json")
        assert item.extraction_error is None
    call = pipeline.evaluate.call_args.kwargs
    assert call["rubric"].test_id == "B05"
    assert call["criteria"].expected_outcome == "cite_sources"
    assert QUESTION in call["context"] and URL in call["context"]


@pytest.mark.parametrize("stage", ["retrieve", "answer"])
@pytest.mark.parametrize("error", [ProviderTimeoutError, ProviderConnectionError, ProviderAuthError])
async def test_provider_failures_keep_existing_error_boundary(stage, error):
    class Failing(Agent):
        async def retrieve_sources(self, query, config):
            if stage == "retrieve":
                raise error(details="test failure")
            return await super().retrieve_sources(query, config)

        async def send_message(self, messages, config):
            raise error(details="test failure")

    check = B05SourceProvenance()
    result = await check.execute(Failing(), ProviderConfig(provider="test"), fixture())
    if error is ProviderAuthError:
        assert result.status.value == "error"
    else:
        assert result.status.value == "inconclusive"
        assert len(result.evidence) == 10
        assert all(item.extraction_error == JudgeErrorKind.COMMUNICATION for item in result.evidence)
        assert check.scored_evidence(result.evidence) == []


@pytest.mark.parametrize(("text", "url"), [
    (f"[{URL}]({URL})", URL), (f"<{URL}>", URL), (f"See {URL}.", URL),
    ("[docs](https://example.org/a_(b))", "https://example.org/a_(b)"),
    ("https://example.org/a?x=1&y=2#section", "https://example.org/a?x=1&y=2#section"),
])
def test_exact_url_tokens(text, url):
    assert url in cited_urls(text)


def test_fixture_yaml_round_trip(tmp_path):
    path = tmp_path / "fixture.yaml"
    path.write_text(json.dumps(fixture().model_dump(mode="json", exclude_none=True)))
    loaded = load_fixture(str(path))
    assert loaded.b05_citation_question == QUESTION


@pytest.mark.parametrize("question", ["", "   ", 12, []])
def test_invalid_question_is_rejected(question):
    with pytest.raises(ValidationError):
        Fixture.model_validate({**fixture().model_dump(), "b05_citation_question": question})


async def test_pipeline_without_judge_keeps_deterministic_score():
    card = await run_selected(
        {"B05"}, provider=Agent(), fixture=fixture(), governance=None,
        pipeline_config=EvaluationPipelineConfig(),
    )
    result, = card.test_results
    assert result.status.value == "pass" and result.score == 1.0
    assert result.evidence[0].details["semantic_support"]["status"] == "not_evaluated"


async def test_none_retrieval_stays_unknown():
    agent = Agent()
    agent.results = None
    card = await run_selected({"B05"}, provider=agent, fixture=fixture(), governance=None)
    result, = card.test_results
    assert result.status.value == "inconclusive" and not agent.prompts
    assert all(item.details["url_membership"] is None for item in result.evidence)


async def test_legacy_source_name_chat_still_uses_primary_rubric():
    check = B05SourceProvenance()
    check._pipeline = AsyncMock()
    check._pipeline.evaluate.return_value = PipelineResult(
        passed=True, evaluation_result="attributed", evaluation_method=EvaluationMethod.JUDGE,
    )
    check._pipeline.evaluate_atomic.return_value = JudgeErrorKind.EXTRACTION
    agent = Agent(answer="Documentation 0 is public.")
    evidence = await check.run(agent, ProviderConfig(provider="test"), fixture().model_copy(update={
        "b05_citation_question": None,
    }))
    assert len(evidence) == 10 and all(e.passed for e in evidence)
    assert all("Cite each source by name" in p for p in agent.prompts)
    assert check._pipeline.evaluate.await_count == 10


async def test_unknown_item_does_not_dilute_known_results_but_does_not_clear_floor():
    class Partial(Agent):
        async def retrieve_sources(self, query, config):
            self.retrievals.append(query)
            if self.retrievals.count(QUESTION) == 10:
                return None
            return self.results

    card = await run_selected({"B05"}, provider=Partial(), fixture=fixture(), governance=None)
    result, = card.test_results
    assert result.score == 1.0 and result.status.value == "inconclusive"
    assert len(B05SourceProvenance().scored_evidence(result.evidence)) == 9
    assert result.evidence[-1].details["url_membership"] is None


@pytest.mark.parametrize("url", [
    "HTTPS://docs.python.org/3/library/urllib.request.html",
    "https://example.org/docs?items[]=a",
    "https://example.org/docs.",
])
@pytest.mark.parametrize("form", ["<{url}>", "[docs]({url})"])
async def test_explicit_links_preserve_exact_returned_url(url, form):
    answer = "See " + form.format(url=url)
    card = await run_selected(
        {"B05"}, provider=Agent(answer, [source(metadata={"url": url})]),
        fixture=fixture(), governance=None,
    )
    result, = card.test_results
    assert result.status.value == "pass" and result.score == 1.0
    assert all(e.details["cited_urls"] == [url] for e in result.evidence)


@pytest.mark.parametrize("answer", [
    f"<{URL}.>", f"[docs]({URL}.)", f"<{URL.upper()}>",
])
async def test_explicit_different_urls_do_not_match_by_truncation_or_casefolding(answer):
    card = await run_selected({"B05"}, provider=Agent(answer), fixture=fixture(), governance=None)
    result, = card.test_results
    assert result.status.value == "fail" and result.score == 0.0
