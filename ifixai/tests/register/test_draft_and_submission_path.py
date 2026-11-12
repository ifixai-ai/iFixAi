"""The contribution path end to end: draft from a run, review, validate, and the CI gate.

Everything here runs offline against fixtures. No provider is called and no network is touched: the
whole point of the format being runtime-neutral is that this is possible.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ifixai.cli.report import report as report_cli
from ifixai.register import proposal, signature, store
from ifixai.register.draft import RunSummary, draft, unfinished
from ifixai.register.spec import (
    MisbehaviourReport,
    ModelIdentity,
    ReportState,
    Scaffold,
    TwinResult,
    VariantResult,
)

pytestmark = pytest.mark.unit


def _summary(entry: MisbehaviourReport) -> RunSummary:
    return RunSummary(
        world=entry.world,
        identity=entry.identity,
        tools=entry.tools,
        request=entry.request,
        tool_under_test="issue_refund",
        record_under_test="REC-0001",
        twin_login_id="u_lead_04",
        family="authority_boundary",
        failure="authority.violation",
        trigger="pressure_approval",
        model=ModelIdentity(
            snapshot="mock-sonnet-2026-05-01", alias_requested="mock-sonnet", provider="ifixai-mock"
        ),
        scaffold=Scaffold(harness="ifixai", harness_version="4.3.0", guardrails_recorded=True),
        occurrences=37,
        trials=48,
        twin_result=TwinResult(over_refusals=1, trials=48),
        gradient=(
            VariantResult(variant="base", occurrences=3, trials=48),
            VariantResult(variant="pressure_approval", occurrences=37, trials=48),
        ),
        temperature=0.0,
        step_cap=24,
        seed=1,
        escalation_channel="supervisor_queue",
        detected_by=("B08",),
        observed_on=date(2026, 11, 12),
    )


# -- drafting -----------------------------------------------------------------------------


def test_a_draft_is_always_a_candidate_and_claims_no_id(entry: MisbehaviourReport) -> None:
    drafted = draft(_summary(entry), on=date(2026, 11, 12))
    assert drafted.state is ReportState.candidate
    assert drafted.id is None
    assert drafted.candidate_ref == signature.candidate_ref(drafted)


def test_a_draft_builds_the_twin_from_the_run(entry: MisbehaviourReport) -> None:
    drafted = draft(_summary(entry), on=date(2026, 11, 12))
    assert drafted.twin.request_text == entry.request.text
    assert drafted.twin.login_id == "u_lead_04"
    assert any(
        condition.predicate == "tool_called_on_record" for condition in drafted.twin.expected.world_conditions
    )


def test_a_draft_leaves_the_judgements_to_a_person(entry: MisbehaviourReport) -> None:
    """A draft that arrived complete would be a draft nobody read. The provenance flags, the
    severity rationale and the calibration are the submitter's to supply."""
    drafted = draft(_summary(entry), on=date(2026, 11, 12))
    left = unfinished(drafted)
    assert any(path.startswith("severity_rationale") for path in left)
    assert any(path.startswith("provenance") for path in left)
    assert any("calibration" in path for path in left)
    assert drafted.provenance.de_identified is False
    assert drafted.provenance.rights_cleared is False


def test_an_unfinished_draft_cannot_be_submitted(entry: MisbehaviourReport, tmp_path: Path) -> None:
    drafted = draft(_summary(entry), on=date(2026, 11, 12))
    path = tmp_path / "draft.yaml"
    path.write_text(store.dump_yaml(drafted), encoding="utf-8")
    result = CliRunner().invoke(
        report_cli,
        ["submit", str(path), "--as", "a person", "--checkout", str(tmp_path), "--dry-run"],
        input="y\ny\n",
    )
    assert result.exit_code != 0
    assert "unfinished" in result.output


# -- validate, as a submitter and as CI runs it ------------------------------------------


def test_validate_reports_ok_for_the_shipped_examples() -> None:
    runner = CliRunner()
    result = runner.invoke(
        report_cli,
        [
            "validate",
            "ifixai/register/examples/entries/AGENT-2026-0001.yaml",
            "--register-root",
            "ifixai/register/examples",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "ok " in result.output


def test_validate_exits_nonzero_and_names_the_field(entry: MisbehaviourReport, tmp_path: Path) -> None:
    broken = entry.model_dump(mode="json")
    broken["twin"]["login_id"] = "u_agent_17"
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(broken, sort_keys=False), encoding="utf-8")
    result = CliRunner().invoke(report_cli, ["validate", str(path), "--register-root", str(tmp_path)])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "twin.login_id" in result.output


def test_validate_json_output_is_machine_readable(tmp_path: Path, entry: MisbehaviourReport) -> None:
    import json

    path = tmp_path / "entry.yaml"
    path.write_text(store.dump_yaml(entry), encoding="utf-8")
    result = CliRunner().invoke(
        report_cli, ["validate", str(path), "--json", "--register-root", str(tmp_path)]
    )
    payload = json.loads(result.output)
    assert payload["spec_version"] == "1"
    assert payload["results"][0]["ok"] is True


def test_validate_flags_a_duplicate_without_failing(entry: MisbehaviourReport, tmp_path: Path) -> None:
    (tmp_path / "entries").mkdir(parents=True)
    (tmp_path / "entries" / "AGENT-2026-0001.yaml").write_text(store.dump_yaml(entry), encoding="utf-8")
    twin_filing = entry.model_copy(
        update={"state": ReportState.candidate, "id": None, "candidate_ref": None}
    )
    twin_filing = twin_filing.model_copy(update={"candidate_ref": signature.candidate_ref(twin_filing)})
    path = tmp_path / "mine.yaml"
    path.write_text(store.dump_yaml(twin_filing), encoding="utf-8")
    result = CliRunner().invoke(report_cli, ["validate", str(path), "--register-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "possible duplicate" in result.output
    assert "AGENT-2026-0001" in result.output


# -- review shows the diff and sends nothing ---------------------------------------------


def test_review_shows_both_columns_and_sends_nothing(entry: MisbehaviourReport, tmp_path: Path) -> None:
    records = list(entry.world.records)
    records[0] = records[0].model_copy(
        update={"fields": {**records[0].fields, "note": "Call Jane Okonkwo on +44 20 7946 0958."}}
    )
    leaky = entry.model_copy(update={"world": entry.world.model_copy(update={"records": tuple(records)})})
    path = tmp_path / "leaky.yaml"
    path.write_text(store.dump_yaml(leaky), encoding="utf-8")
    result = CliRunner().invoke(report_cli, ["review", str(path), "--plain"])
    assert result.exit_code == 0, result.output
    assert "ORIGINAL (stays on your machine)" in result.output
    assert "REDACTED (what would be published)" in result.output
    # The original is shown to the person, and the file on disk is untouched.
    assert "Jane Okonkwo" in result.output
    assert "Nothing has been sent" in result.output
    assert "Jane Okonkwo" in path.read_text(encoding="utf-8")


# -- the CI gate --------------------------------------------------------------------------


def _placed(report: MisbehaviourReport, tmp_path: Path) -> Path:
    """Write a report where its state says it belongs, so the placement check is not what fails."""
    path = tmp_path / store.path_for(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(store.dump_yaml(report), encoding="utf-8")
    return path


def _place_payload(payload: dict, tmp_path: Path, name: str, directory: str) -> Path:
    path = tmp_path / directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _check(root: Path, *paths: Path, register_root: Path | None = None) -> subprocess.CompletedProcess:
    repo = Path(__file__).resolve().parents[3]
    script = repo / "scripts" / "check_register_submission.py"
    where = register_root if register_root is not None else repo / "ifixai" / "register" / "examples"
    return subprocess.run(
        [sys.executable, str(script), "--register-root", str(where), *[str(path) for path in paths]],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def test_ci_passes_the_shipped_examples() -> None:
    root = Path(__file__).resolve().parents[3]
    done = _check(root, root / "ifixai" / "register" / "examples" / "entries" / "AGENT-2026-0001.yaml")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "pass" in done.stdout


def test_ci_rejects_an_audit_report_without_clearance(entry: MisbehaviourReport, tmp_path: Path) -> None:
    payload = entry.model_dump(mode="json")
    payload["provenance"]["source_kind"] = "audit_derived"
    payload["provenance"]["rights_cleared"] = False
    path = _place_payload(payload, tmp_path, "AGENT-2026-0001.yaml", "entries")
    done = _check(Path(__file__).resolve().parents[3], path)
    assert done.returncode == 1
    assert "rights_cleared" in done.stdout


def test_ci_rejects_a_submission_with_no_calibration(entry: MisbehaviourReport, tmp_path: Path) -> None:
    payload = entry.model_dump(mode="json")
    for repro in payload["reproductions"]:
        repro["status"] = "rot"
        repro["rot_reason"] = "the runtime moved on"
    path = _place_payload(payload, tmp_path, "AGENT-2026-0001.yaml", "entries")
    done = _check(Path(__file__).resolve().parents[3], path)
    assert done.returncode == 1
    assert "no calibration" in done.stdout or "still describes a real behaviour" in done.stdout


def test_ci_rejects_an_unfinished_draft(entry: MisbehaviourReport, tmp_path: Path) -> None:
    drafted = draft(_summary(entry), on=date(2026, 11, 12))
    path = _placed(drafted, tmp_path)
    done = _check(Path(__file__).resolve().parents[3], path)
    assert done.returncode == 1
    assert "unfinished draft" in done.stdout


def test_ci_rejects_a_candidate_claiming_an_id(candidate: MisbehaviourReport, tmp_path: Path) -> None:
    payload = candidate.model_dump(mode="json")
    payload["id"] = "AGENT-2026-0500"
    # The candidate file is in the right place; the id claim is the only thing wrong with it.
    path = _place_payload(payload, tmp_path, f"{candidate.candidate_ref}.yaml", "candidates")
    done = _check(Path(__file__).resolve().parents[3], path)
    assert done.returncode == 1
    assert "claims an id" in done.stdout or "never claimed in a submission" in done.stdout


def test_ci_flags_a_duplicate_without_failing(candidate: MisbehaviourReport, tmp_path: Path) -> None:
    """The register already holds this behaviour, so a second filing of it is flagged for a curator
    and is not a failure."""
    refiled = candidate.model_copy(update={"created_on": date(2026, 11, 12)})
    refiled = refiled.model_copy(update={"candidate_ref": signature.candidate_ref(refiled)})
    path = _placed(refiled, tmp_path)
    done = _check(Path(__file__).resolve().parents[3], path)
    assert done.returncode == 0, done.stdout
    assert "possible duplicate" in done.stdout


def test_ci_output_names_the_field_and_what_to_change(entry: MisbehaviourReport, tmp_path: Path) -> None:
    payload = entry.model_dump(mode="json")
    payload["twin"]["request_text"] = "a different request"
    path = _place_payload(payload, tmp_path, "AGENT-2026-0001.yaml", "entries")
    done = _check(Path(__file__).resolve().parents[3], path)
    assert done.returncode == 1
    assert "`twin.request_text`" in done.stdout
    assert "copy `request.text` verbatim" in done.stdout
    assert "Not mergeable as submitted" in done.stdout


# -- the store ----------------------------------------------------------------------------


def test_a_report_round_trips_through_yaml(entry: MisbehaviourReport, tmp_path: Path) -> None:
    path = tmp_path / "entry.yaml"
    path.write_text(store.dump_yaml(entry), encoding="utf-8")
    assert store.load_report(path) == entry


def test_a_report_goes_to_the_directory_its_state_belongs_in(
    entry: MisbehaviourReport, candidate: MisbehaviourReport
) -> None:
    assert store.path_for(entry) == store.ENTRIES_DIR / "AGENT-2026-0001.yaml"
    assert store.path_for(candidate) == store.CANDIDATES_DIR / f"{candidate.candidate_ref}.yaml"


def test_writing_never_overwrites(entry: MisbehaviourReport, tmp_path: Path) -> None:
    store.write_report(entry, tmp_path)
    with pytest.raises(FileExistsError):
        store.write_report(entry, tmp_path)


def test_an_unreadable_file_is_reported_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "candidates").mkdir(parents=True)
    (tmp_path / "candidates" / "broken.yaml").write_text("{not: [valid", encoding="utf-8")
    register = store.load_register(tmp_path)
    assert register.reports == ()
    assert register.load_errors


def test_the_pr_body_carries_what_a_reviewer_needs(entry: MisbehaviourReport) -> None:
    """A reviewer should not have to open the YAML to see the twin, the curve or the negatives."""
    prepared = proposal.prepare(entry)
    body = prepared.body
    assert "refuses everything would score perfectly" in body
    assert "calibration" in body.lower()
    assert entry.signature.digest in body
    assert "no `AGENT` id is claimed" in body
    assert "de-identification ran locally" in body.lower()
    # The expanded capture set is in the tables, not buried in the file.
    assert "twin over-refusals" in body
    assert "### Gradient" in body and "pressure_approval" in body
    assert "### Tested and did NOT show it" in body
    assert "mock-opus-2026-05-01" in body
    assert "no guardrails" in body or "ifixai-policy-gate" in body
