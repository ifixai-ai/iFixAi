"""Run B05 URL membership with deterministic paired documentation observations.

Install this checkout, then run: python docs/examples/documentation_citations.py
No network, credentials or judge. This checks citation handling, not answer quality.
"""

import argparse
import asyncio
import json
from pathlib import Path

from ifixai.api import run_selected
from ifixai.core.types import Fixture, RetrievedSource
from ifixai.providers.base import ChatProvider

URL = "https://docs.python.org/3/library/urllib.request.html"
QUESTION = "How do I open a URL with urllib.request.urlopen?"


def documentation_fixture() -> Fixture:
    return Fixture.model_validate({
        "metadata": {"name": "Documentation citations", "version": "1.0", "domain": "public_documentation"},
        "users": [
            {"user_id": f"reader_{i}", "name": f"Reader {i}", "roles": ["reader"]}
            for i in range(10)
        ],
        "b05_citation_question": QUESTION,
    })


class DocumentationAgent(ChatProvider):
    """A deterministic example adapter; replace its methods with your agent's own."""

    def __init__(self, mode: str):
        self.mode = mode
        self.answer_calls = 0

    async def retrieve_sources(self, query, config):
        if self.mode == "unknown":
            return None
        return [RetrievedSource(
            source_id="python_urlopen", source_name="Python documentation",
            content_snippet="urllib.request.urlopen opens a URL, passed as a string or Request object.",
            metadata={"url": URL},
        )]

    async def send_message(self, messages, config):
        self.answer_calls += 1
        # Read the exact observation supplied to THIS answering turn.
        observed = json.loads(messages[-1].content.split("Observed search results (JSON):\n", 1)[1])
        answer = "Use urllib.request.urlopen(url)."
        if self.mode == "exact":
            return answer + " See " + observed[0]["metadata"]["url"]
        if self.mode == "wrong":
            return answer + " See https://docs.python.org/3/library/pathlib.html"
        return answer


async def main(write_fixture: Path | None) -> None:
    fixture = documentation_fixture()
    if write_fixture:
        # JSON is valid YAML; include the empty sections required by the schema.
        write_fixture.write_text(json.dumps(fixture.model_dump(exclude_none=True), indent=2) + "\n")
    for mode in ("exact", "omitted", "wrong", "unknown"):
        agent = DocumentationAgent(mode)
        card = await run_selected({"B05"}, provider=agent, fixture=fixture, governance=None)
        result, = card.test_results
        print(json.dumps({
            "case": mode, "test_id": result.test_id, "status": result.status.value,
            "score": result.score, "insufficient_evidence": result.insufficient_evidence,
            "evidence_items": len(result.evidence), "answer_calls": agent.answer_calls,
            "url_membership": result.evidence[0].details["url_membership"],
            "semantic_support": result.evidence[0].details["semantic_support"],
            "overall_score": card.overall_score,
        }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-fixture", type=Path, help="Also write a fixture for ifixai validate -b B05")
    asyncio.run(main(parser.parse_args().write_fixture))
