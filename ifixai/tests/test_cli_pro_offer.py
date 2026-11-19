"""The hosted-audit offer printed at the start and the end of an ``ifixai run``."""

import io
import json
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import ifixai.cli.run as run_module
from ifixai import telemetry
from ifixai.cli import pro_offer
from ifixai.cli.main import ifixai_cli
from ifixai.cli.pro_offer import NO_PROMO_ENV_VAR
from ifixai.core.types import TestResult, TestRunResult, TestStatus

UNKNOWN_TEST_ID = "ZZ99"
UNKNOWN_TEST_ERROR = "unknown test ID"
REAL_PROVIDER = "openai"
CONNECTOR_URL = "https://mcp.ifixai.ai/mcp"
EXPECTED_LINES = [
    "One free hosted audit for you: up to 12 inspections the open-source set does not "
    "have, 20 minutes at most.",
    "Add iFixAi to your coding agent: claude mcp add --transport http --scope user "
    "ifixai https://mcp.ifixai.ai/mcp",
    "Then say: audit my agent with code MYAUDIT",
]
SKIPPED_RUNS = [
    (["--no-promo"], {}),
    ([], {NO_PROMO_ENV_VAR: "1"}),
    (["--dry-run"], {}),
]


class FakeStream(io.StringIO):
    """A text stream that reports whether it is attached to a terminal."""

    def __init__(self, is_terminal: bool) -> None:
        super().__init__()
        self.is_terminal = is_terminal

    def isatty(self) -> bool:
        return self.is_terminal


class EventRecorder:
    """Stands in for ``telemetry.emit_offer_shown`` and records each place."""

    def __init__(self) -> None:
        self.places: list[str] = []

    def __call__(self, where: str) -> None:
        self.places.append(where)


class SenderRecorder:
    """Stands in for the telemetry sender and keeps every body it was given."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, object]] = []

    def submit(self, body: bytes) -> None:
        self.bodies.append(json.loads(body))


class CallRecorder:
    """Stands in for a run hook and records its calls in a shared order list."""

    def __init__(self, name: str, order: list[str], reports: Path | None = None) -> None:
        self.name = name
        self.order = order
        self.reports = reports
        self.calls: list[tuple[object, ...]] = []
        self.reports_on_disk: list[bool] = []

    def __call__(self, *args: object) -> None:
        self.calls.append(args)
        self.order.append(self.name)
        if self.reports is not None:
            self.reports_on_disk.append(bool(list(self.reports.glob("*.json"))))


def report_outside_ci() -> bool:
    return False


def report_terminal_attached() -> bool:
    return True


def report_terminal_attached_for(stream: object) -> bool:
    return True


def run_result(*statuses: TestStatus) -> TestRunResult:
    return TestRunResult(
        test_results=[
            TestResult(test_id=f"B{index:02d}", status=status)
            for index, status in enumerate(statuses, start=1)
        ]
    )


def offer_lines(text: str) -> list[str]:
    return [line for line in click.unstyle(text).splitlines() if line in EXPECTED_LINES]


@pytest.fixture(autouse=True)
def offer_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the shown-once marker lands; kept in ``tmp_path``, never the real home."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "ifixai" / "pro-offer-shown"


@pytest.fixture
def shown_events(monkeypatch: pytest.MonkeyPatch) -> EventRecorder:
    recorder = EventRecorder()
    monkeypatch.setattr(telemetry, "emit_offer_shown", recorder)
    return recorder


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every stream counts as a terminal, outside CI; the output itself is read with capsys."""
    monkeypatch.setattr(pro_offer, "is_terminal", report_terminal_attached_for)
    monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)


@pytest.fixture
def run_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A ``run`` invocation that stops at validation, before any inspection."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(NO_PROMO_ENV_VAR, raising=False)
    return [
        "run", "--provider", "mock", "--eval-mode", "self",
        "--test", UNKNOWN_TEST_ID, "--no-telemetry",
    ]


@pytest.fixture
def mock_run_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A ``run`` invocation that executes one offline inspection into ``tmp_path``."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(NO_PROMO_ENV_VAR, raising=False)
    return [
        "run", "--provider", "mock", "--fixture", "customer_support",
        "--test", "B01", "--eval-mode", "self", "--no-telemetry", "--no-parallel",
        "--output", str(tmp_path / "reports"),
        "--reliability-out", str(tmp_path / "runs"),
    ]


def test_the_start_offer_prints_the_three_lines_on_stderr_once_per_machine(
    terminal: None,
    shown_events: EventRecorder,
    offer_marker: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pro_offer.offer_at_start(REAL_PROVIDER)
    pro_offer.offer_at_start(REAL_PROVIDER)

    printed = capsys.readouterr()
    assert offer_lines(printed.err) == EXPECTED_LINES
    assert printed.out == ""
    assert offer_marker.exists()
    assert shown_events.places == ["start"]


def test_the_end_offer_prints_after_every_real_run_with_a_verdict(
    terminal: None,
    shown_events: EventRecorder,
    offer_marker: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    offer_marker.parent.mkdir(parents=True)
    offer_marker.touch()

    pro_offer.offer_at_end(REAL_PROVIDER, run_result(TestStatus.PASS))
    pro_offer.offer_at_end(REAL_PROVIDER, run_result(TestStatus.FAIL, TestStatus.ERROR))

    printed = capsys.readouterr()
    assert offer_lines(printed.err) == EXPECTED_LINES * 2
    assert printed.out == ""
    assert shown_events.places == ["end", "end"]


@pytest.mark.parametrize(
    "statuses",
    [(), (TestStatus.INCONCLUSIVE,), (TestStatus.ERROR, TestStatus.INCONCLUSIVE)],
)
def test_the_end_offer_needs_at_least_one_verdict(
    terminal: None,
    shown_events: EventRecorder,
    statuses: tuple[TestStatus, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    pro_offer.offer_at_end(REAL_PROVIDER, run_result(*statuses))

    assert capsys.readouterr().err == ""
    assert shown_events.places == []


@pytest.mark.parametrize("provider", ["mock", "MOCK"])
def test_the_mock_agent_never_sees_the_offer(
    terminal: None,
    shown_events: EventRecorder,
    offer_marker: Path,
    provider: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pro_offer.offer_at_start(provider)
    pro_offer.offer_at_end(provider, run_result(TestStatus.PASS))

    assert capsys.readouterr().err == ""
    assert not offer_marker.exists()
    assert shown_events.places == []


@pytest.mark.parametrize(
    "is_stdout_terminal,is_stderr_terminal,expected",
    [(True, True, True), (False, True, False), (True, False, False)],
)
def test_the_offer_needs_a_terminal_on_both_output_streams(
    monkeypatch: pytest.MonkeyPatch,
    is_stdout_terminal: bool,
    is_stderr_terminal: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)
    monkeypatch.setattr(sys, "stdout", FakeStream(is_stdout_terminal))
    monkeypatch.setattr(sys, "stderr", FakeStream(is_stderr_terminal))

    assert pro_offer.person_at_terminal() is expected


@pytest.mark.parametrize("missing_stream", ["stdout", "stderr"])
def test_a_missing_standard_stream_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch, missing_stream: str
) -> None:
    monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)
    monkeypatch.setattr(sys, "stdout", FakeStream(is_terminal=True))
    monkeypatch.setattr(sys, "stderr", FakeStream(is_terminal=True))
    monkeypatch.setattr(sys, missing_stream, None)

    assert pro_offer.person_at_terminal() is False


def test_ci_never_sees_the_offer_even_with_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
    shown_events: EventRecorder,
    offer_marker: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(pro_offer, "is_terminal", report_terminal_attached_for)

    pro_offer.offer_at_start(REAL_PROVIDER)
    pro_offer.offer_at_end(REAL_PROVIDER, run_result(TestStatus.PASS))

    assert capsys.readouterr().err == ""
    assert not offer_marker.exists()
    assert shown_events.places == []


@pytest.mark.parametrize("blocking_file", ["config", "config/ifixai"])
def test_a_marker_that_cannot_be_written_skips_the_start_lines(
    tmp_path: Path,
    terminal: None,
    shown_events: EventRecorder,
    blocking_file: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A file where the config dir should be: the marker can't be created.
    blocker = tmp_path / blocking_file
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory", encoding="utf-8")

    pro_offer.offer_at_start(REAL_PROVIDER)

    assert capsys.readouterr().err == ""
    assert shown_events.places == []


def test_the_lines_name_the_connector_and_no_web_page() -> None:
    lines = [pro_offer.OFFER_HEADLINE, pro_offer.OFFER_COMMAND_LINE, pro_offer.OFFER_SAY_LINE]
    urls = [word for line in lines for word in line.split() if "://" in word]

    assert lines == EXPECTED_LINES
    assert urls == [CONNECTOR_URL]
    assert not any("dashboard" in line.lower() for line in lines)


def test_the_run_offers_at_the_start_and_after_the_saved_reports(
    tmp_path: Path, mock_run_args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    start = CallRecorder("start", order)
    end = CallRecorder("end", order, reports=tmp_path / "reports")
    monkeypatch.setattr(run_module, "offer_at_start", start)
    monkeypatch.setattr(run_module, "offer_at_end", end)
    monkeypatch.setattr(telemetry, "emit_completed", CallRecorder("completed", order))

    result = CliRunner().invoke(ifixai_cli, mock_run_args)

    assert order == ["start", "end", "completed"], result.output
    assert start.calls == [("mock",)]
    assert end.calls[0][0] == "mock"
    assert isinstance(end.calls[0][1], TestRunResult)
    assert end.reports_on_disk == [True]


@pytest.mark.parametrize("extra_args,env", SKIPPED_RUNS)
def test_the_run_skips_the_offer_when_told_to_or_when_nothing_will_run(
    run_args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    env: dict[str, str],
) -> None:
    order: list[str] = []
    monkeypatch.setattr(run_module, "offer_at_start", CallRecorder("start", order))
    monkeypatch.setattr(run_module, "offer_at_end", CallRecorder("end", order))

    result = CliRunner().invoke(ifixai_cli, [*run_args, *extra_args], env=env)

    assert order == []
    assert UNKNOWN_TEST_ERROR in result.output


def test_no_promo_skips_the_end_offer_of_a_finished_run(
    mock_run_args: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(run_module, "offer_at_start", CallRecorder("start", order))
    monkeypatch.setattr(run_module, "offer_at_end", CallRecorder("end", order))

    result = CliRunner().invoke(ifixai_cli, [*mock_run_args, "--no-promo"])

    assert "Reports saved" in result.output
    assert order == []


def test_a_run_without_a_terminal_never_shows_or_marks_the_offer(
    mock_run_args: list[str], offer_marker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Not the mock agent, so only the missing terminal keeps the lines away.
    monkeypatch.setattr(pro_offer, "MOCK_PROVIDER", "no-such-provider")

    result = CliRunner().invoke(ifixai_cli, mock_run_args)

    assert "Reports saved" in result.output
    assert offer_lines(result.output) == []
    assert not offer_marker.exists()


def test_a_real_run_at_a_terminal_starts_and_ends_on_the_offer(
    tmp_path: Path, mock_run_args: list[str], offer_marker: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The mock agent stands in for a real one here; the terminal check is answered yes.
    monkeypatch.setattr(pro_offer, "MOCK_PROVIDER", "no-such-provider")
    monkeypatch.setattr(pro_offer, "person_at_terminal", report_terminal_attached)

    first = CliRunner().invoke(ifixai_cli, mock_run_args)
    second = CliRunner().invoke(ifixai_cli, mock_run_args)

    assert offer_lines(first.output) == EXPECTED_LINES * 2, first.output
    assert first.output.index("Reports saved") < first.output.rindex(EXPECTED_LINES[0])
    assert offer_marker.exists()
    assert offer_lines(second.output) == EXPECTED_LINES, second.output
    assert second.output.index("Reports saved") < second.output.index(EXPECTED_LINES[0])
    for report in (tmp_path / "reports").iterdir():
        assert "MYAUDIT" not in report.read_text(encoding="utf-8")


class TestOfferTelemetry:
    @pytest.fixture
    def sender(self, monkeypatch: pytest.MonkeyPatch) -> SenderRecorder:
        recorder = SenderRecorder()
        for name in ("IFIXAI_TELEMETRY", "DO_NOT_TRACK"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(telemetry, "in_ci", report_outside_ci)
        monkeypatch.setattr(telemetry, "_force_disabled", False)
        monkeypatch.setattr(telemetry, "_offers_emitted", set())
        monkeypatch.setattr(telemetry, "_get_sender", lambda: recorder)
        return recorder

    def test_each_place_is_sent_once_with_where_it_was(self, sender: SenderRecorder) -> None:
        telemetry.emit_offer_shown("start")
        telemetry.emit_offer_shown("end")
        telemetry.emit_offer_shown("end")

        assert [body["event"] for body in sender.bodies] == ["ifixai_offer_shown"] * 2
        assert [body["properties"]["where"] for body in sender.bodies] == ["start", "end"]

    def test_an_unknown_place_is_never_sent(self, sender: SenderRecorder) -> None:
        telemetry.emit_offer_shown("/home/someone/project")

        assert sender.bodies == []

    def test_other_events_carry_no_where(self, sender: SenderRecorder) -> None:
        telemetry._enqueue("ifixai_started")

        assert "where" not in sender.bodies[0]["properties"]

    def test_the_opt_out_sends_nothing(
        self, sender: SenderRecorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("IFIXAI_TELEMETRY", "0")

        telemetry.emit_offer_shown("start")

        assert sender.bodies == []
