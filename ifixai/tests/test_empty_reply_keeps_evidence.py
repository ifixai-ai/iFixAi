"""One empty reply from the agent costs one probe, not the whole inspection.

A reasoning model that spends its token budget thinking returns empty content with
finish_reason=length. Each inspection here is driven through the real CLI against a loopback
OpenRouter-shaped endpoint that answers normally except where the test says otherwise.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, TypedDict

import pytest
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli
from ifixai.harness.registry import SPEC_BY_ID

pytest.importorskip("openai")

CONNECTION_PROBE = [{"role": "user", "content": "Hello"}]
NORMAL_REPLY = "I cannot verify that claim. Please consult an authoritative source."
# Past the capability probes every run sends first, inside each inspection's own traffic.
EMPTY_REPLY_POSITION = 6
# Every graded core inspection that once lost all evidence to a single empty reply.
ONCE_FRAGILE_IDS = [
    "B05", "B06", "B07", "B08", "B09", "B10", "B12", "B13", "B14", "B16",
    "B18", "B21", "B24", "B25", "B26", "B28", "B30",
]
B16_PROBE_COUNT = 30
# An unanswered turn also voids the later turns of its own conversation; B21's plan, the
# longest here, has four.
LONGEST_PLAN_STEPS = 4


class EmptyReplyPlan(TypedDict):
    """Which inspection requests the loopback agent answers with empty content."""

    positions: set[int]
    is_every_reply_empty: bool


class LoopbackAgent(TypedDict):
    base_url: str
    plan: EmptyReplyPlan
    request_count: list[int]


class LoopbackHandler(BaseHTTPRequestHandler):
    plan: ClassVar[EmptyReplyPlan] = {"positions": set(), "is_every_reply_empty": False}
    request_count: ClassVar[list[int]] = [0]
    count_lock = threading.Lock()

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        is_empty = False
        if body.get("messages") != CONNECTION_PROBE:
            with self.count_lock:
                self.request_count[0] += 1
                position = self.request_count[0]
            is_empty = self.plan["is_every_reply_empty"] or position in self.plan["positions"]
        payload = json.dumps(
            {
                "id": "loopback",
                "object": "chat.completion",
                "created": 0,
                "model": "loopback-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "length" if is_empty else "stop",
                        "message": {"role": "assistant", "content": "" if is_empty else NORMAL_REPLY},
                    }
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def loopback_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LoopbackAgent]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("IFIXAI_TELEMETRY", "0")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    plan: EmptyReplyPlan = {"positions": set(), "is_every_reply_empty": False}
    request_count = [0]
    LoopbackHandler.plan = plan
    LoopbackHandler.request_count = request_count
    server = ThreadingHTTPServer(("127.0.0.1", 0), LoopbackHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield {
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "plan": plan,
            "request_count": request_count,
        }
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def run_inspection(agent: LoopbackAgent, test_id: str) -> dict:
    """Run one inspection through the CLI and return its entry from the JSON report."""
    return run_report(agent, test_id)["test_results"][0]


def run_report(agent: LoopbackAgent, test_id: str) -> dict:
    """Run one inspection through the CLI and return the whole JSON report."""
    result = CliRunner().invoke(
        ifixai_cli,
        [
            "run", "--provider", "openrouter", "--endpoint", agent["base_url"],
            "--api-key", "synthetic-key", "--model", "loopback-model",
            "--eval-mode", "single", "--judge-provider", "mock", "--judge-api-key", "unused",
            "--test", test_id, "--format", "json", "--min-score", "0",
            "--no-telemetry", "--no-promo", "--no-parallel",
        ],
    )
    reports = list(Path("ifixai-results").glob("*.json"))
    assert len(reports) == 1, result.output
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert [row["test_id"] for row in report["test_results"]] == [test_id]
    return report


def count_unreachable(inspection: dict) -> int:
    return sum(1 for item in inspection["evidence"] if item["extraction_error"] == "communication")


def count_graded_replies(inspection: dict) -> int:
    return sum(
        1
        for item in inspection["evidence"]
        if item["extraction_error"] is None and item["evaluation_method"] != "structural"
    )


@pytest.mark.parametrize("test_id", ONCE_FRAGILE_IDS)
def test_one_empty_reply_costs_one_conversation_and_keeps_the_rest(
    loopback_agent: LoopbackAgent, test_id: str
) -> None:
    loopback_agent["plan"]["positions"].add(EMPTY_REPLY_POSITION)
    inspection = run_inspection(loopback_agent, test_id)
    assert loopback_agent["request_count"][0] > EMPTY_REPLY_POSITION
    assert inspection["error"] is None
    assert 1 <= count_unreachable(inspection) <= LONGEST_PLAN_STEPS
    assert count_graded_replies(inspection) >= 10
    assert inspection["status"] in {"pass", "fail"}


@pytest.mark.parametrize("test_id", ONCE_FRAGILE_IDS)
def test_agent_that_only_returns_empty_replies_is_inconclusive(
    loopback_agent: LoopbackAgent, test_id: str
) -> None:
    """Nothing the agent said was graded, so there is no result to report: not a
    manufactured 0%, and not a pass earned by API-level items that need no reply
    (B13, B14 and B25 have those; B16 would otherwise count every probe as a failure)."""
    loopback_agent["plan"]["is_every_reply_empty"] = True
    inspection = run_inspection(loopback_agent, test_id)
    assert count_unreachable(inspection) > 0
    assert inspection["status"] == "inconclusive"
    assert inspection["passing"] is False
    assert inspection["score"] is None


@pytest.mark.parametrize("test_id", ONCE_FRAGILE_IDS)
def test_run_with_only_empty_replies_is_reported_as_a_measurement_failure(
    loopback_agent: LoopbackAgent, test_id: str
) -> None:
    """Whatever one inspection reports, the run says the agent was never measured."""
    loopback_agent["plan"]["is_every_reply_empty"] = True
    report = run_report(loopback_agent, test_id)
    assert count_unreachable(report["test_results"][0]) > 0
    assert any(warning.startswith("run_invalid") for warning in report["validation_warnings"])


def test_b16_charges_the_one_unanswered_request_as_a_failure_not_reported(
    loopback_agent: LoopbackAgent,
) -> None:
    """B16 asks whether a failure was reported out loud, and its spec counts an unscorable
    probe as not reported. One unanswered request therefore costs one probe of the score,
    where the other inspections drop it from the denominator."""
    assert SPEC_BY_ID["B16"].count_extraction_errors_as_fail is True
    loopback_agent["plan"]["positions"].add(EMPTY_REPLY_POSITION)
    inspection = run_inspection(loopback_agent, "B16")
    assert inspection["evidence_count"] == B16_PROBE_COUNT
    assert count_unreachable(inspection) == 1
    assert inspection["score"] == pytest.approx((B16_PROBE_COUNT - 1) / B16_PROBE_COUNT, abs=1e-4)


def test_unanswered_turn_voids_the_later_turns_of_its_conversation_only(
    loopback_agent: LoopbackAgent,
) -> None:
    """B08 runs three-turn conversations. Whatever turn the empty reply lands on, the
    same conversation's later turns are recorded as not sent and every other conversation
    is graded in full, so the evidence count does not change."""
    control = run_inspection(loopback_agent, "B08")
    Path(next(Path("ifixai-results").glob("*.json"))).unlink()
    loopback_agent["request_count"][0] = 0
    loopback_agent["plan"]["positions"].add(EMPTY_REPLY_POSITION)
    inspection = run_inspection(loopback_agent, "B08")
    unreachable = count_unreachable(inspection)
    assert 1 <= unreachable <= 3
    assert inspection["evidence_count"] == control["evidence_count"]
    assert count_graded_replies(inspection) == count_graded_replies(control) - unreachable


@pytest.mark.parametrize("test_id", ONCE_FRAGILE_IDS)
def test_agent_that_always_answers_has_no_unreachable_probe(
    loopback_agent: LoopbackAgent, test_id: str
) -> None:
    """Control: the unreachable count above is caused by the empty reply, not by the harness."""
    inspection = run_inspection(loopback_agent, test_id)
    assert inspection["error"] is None
    assert count_unreachable(inspection) == 0
    assert count_graded_replies(inspection) >= 10
