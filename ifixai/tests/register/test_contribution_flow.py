"""The flow at the moment of discovery: de-identify first, then ask once.

The order is the property under test. Everything that can be done without permission happens
before the question — the report is built, redacted, validated, de-duplicated, and the standing
clearance is read — and nothing touches the network until a person answers.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ifixai.cli.report import report as report_cli
from ifixai.register import clearance as clearance_module
from ifixai.register import prompt, store
from ifixai.register.spec import MisbehaviourReport

pytestmark = pytest.mark.unit

CLEARANCE = {
    "version": "1",
    "organisation": "Acme Health Partners",
    "authorised_by": "R. Mbeki, Head of Platform Security",
    "scope": "Synthetic and de-identified agent misbehaviour findings from our own staging systems.",
    "expires_on": "2027-03-31",
    "excludes": ["anything derived from production customer records"],
}


def _as_fresh_finding(entry: MisbehaviourReport) -> MisbehaviourReport:
    """The entry's behaviour as a run would present it: a candidate, not the filed entry itself.

    A report *is* not its own duplicate, so re-offering the exact filed entry is correctly treated
    as new. What a run actually produces is a fresh candidate carrying the same signature, and that
    is what de-duplication has to catch."""
    from ifixai.register import signature as signature_module
    from ifixai.register.spec import ReportState

    fresh = entry.model_copy(update={"state": ReportState.candidate, "id": None, "candidate_ref": None})
    return fresh.model_copy(update={"candidate_ref": signature_module.candidate_ref(fresh)})


def _leaky(entry: MisbehaviourReport) -> MisbehaviourReport:
    records = list(entry.world.records)
    records[0] = records[0].model_copy(
        update={
            "fields": {
                **records[0].fields,
                "customer_name": "Jane Okonkwo",
                "note": "Jane Okonkwo rang from +44 20 7946 0958 about ACCT-99812736.",
            }
        }
    )
    return entry.model_copy(update={"world": entry.world.model_copy(update={"records": tuple(records)})})


def _write_clearance(root: Path, payload: dict | None = None) -> Path:
    target = root / clearance_module.DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(payload or CLEARANCE, sort_keys=False), encoding="utf-8")
    return target


# -- de-identification happens first -----------------------------------------------------


def test_the_offer_is_built_redacted_before_anything_is_asked(
    entry: MisbehaviourReport, tmp_path: Path, register_root: Path
) -> None:
    offer = prompt.build(_leaky(entry), register_root=register_root, project_root=tmp_path)
    serialised = store.dump_yaml(offer.report)
    for secret in ("Jane Okonkwo", "+44 20 7946 0958", "99812736"):
        assert secret not in serialised
    assert offer.scrubbed.findings


def test_the_headline_shows_the_numbers_and_the_redaction_summary(
    entry: MisbehaviourReport, tmp_path: Path, register_root: Path
) -> None:
    """What the decision rests on, on one screen: the signature, the rates with the twin beside
    them, the curve, and what was replaced."""
    offer = prompt.build(_leaky(entry), register_root=register_root, project_root=tmp_path)
    headline = offer.headline()
    assert offer.report.signature.digest in headline
    assert "37/48" in headline
    assert "permitted twin over-refused" in headline
    assert "pressure_approval" in headline
    assert "did NOT show it" in headline
    assert "De-identified locally" in headline
    assert "replaced" in headline
    # The originals are not in the headline; the full diff is behind --show.
    assert "Jane Okonkwo" not in headline


def test_show_renders_the_full_side_by_side(entry: MisbehaviourReport, tmp_path: Path, register_root: Path) -> None:
    offer = prompt.build(_leaky(entry), register_root=register_root, project_root=tmp_path)
    shown = io.StringIO()
    prompt.ask(offer, granted_by="a person", writer=shown, reader=lambda: "n", show_diff=True)
    out = shown.getvalue()
    assert "ORIGINAL (stays on your machine)" in out
    assert "Jane Okonkwo" in out, "the person reviewing is shown the original; the agent is not"


# -- one question, defaulting to no ------------------------------------------------------


def test_declining_is_the_default_and_sends_nothing(
    entry: MisbehaviourReport, tmp_path: Path, register_root: Path
) -> None:
    offer = prompt.build(entry, register_root=register_root, project_root=tmp_path)
    shown = io.StringIO()
    assert prompt.ask(offer, granted_by="a person", writer=shown, reader=lambda: "") is None
    assert "Nothing has been sent" in shown.getvalue()


def test_a_valid_clearance_makes_it_one_keystroke(
    entry: MisbehaviourReport, tmp_path: Path, register_root: Path
) -> None:
    _write_clearance(tmp_path)
    offer = prompt.build(
        entry, register_root=register_root, project_root=tmp_path, today=date(2026, 11, 12)
    )
    assert offer.pre_cleared
    answers = iter(["y"])
    shown = io.StringIO()
    approval = prompt.ask(offer, granted_by="a person", writer=shown, reader=lambda: next(answers, ""))
    assert approval is not None
    assert approval.pre_clearance is not None
    assert shown.getvalue().count("[y/N]") == 1


def test_without_a_clearance_rights_are_asked_per_submission(
    entry: MisbehaviourReport, tmp_path: Path, register_root: Path
) -> None:
    offer = prompt.build(entry, register_root=register_root, project_root=tmp_path)
    assert not offer.pre_cleared
    answers = iter(["y", "y"])
    shown = io.StringIO()
    approval = prompt.ask(offer, granted_by="a person", writer=shown, reader=lambda: next(answers, ""))
    assert approval is not None
    assert approval.pre_clearance is None
    assert shown.getvalue().count("[y/N]") == 2


def test_the_offer_only_fires_for_an_unknown_behaviour(
    entry: MisbehaviourReport, candidate: MisbehaviourReport, register_root: Path, tmp_path: Path
) -> None:
    """A prompt on every run is a prompt people learn to ignore."""
    known = prompt.build(_as_fresh_finding(entry), register_root=register_root, project_root=tmp_path)
    assert not prompt.should_offer(known), "this behaviour is already filed; a run must not interrupt"
    novel = entry.model_copy(
        update={
            "signature": entry.signature.model_copy(update={"trigger": "a_condition_nobody_has_filed"}),
        }
    )
    from ifixai.register import signature as signature_module

    novel = novel.model_copy(update={"signature": signature_module.with_digest(novel.signature)})
    fresh = prompt.build(novel, register_root=register_root, project_root=tmp_path)
    assert prompt.should_offer(fresh)


# -- the clearance file -------------------------------------------------------------------


def test_a_clearance_is_read_from_the_project(tmp_path: Path) -> None:
    _write_clearance(tmp_path)
    found = clearance_module.load(tmp_path)
    assert found is not None
    assert found.authorised_by.startswith("R. Mbeki")
    assert found.valid_on(date(2026, 11, 12))
    assert not found.valid_on(date(2028, 1, 1))
    assert "production customer records" in found.excludes[0]


def test_no_clearance_is_not_an_error(tmp_path: Path) -> None:
    assert clearance_module.load(tmp_path) is None


@pytest.mark.parametrize(
    "broken",
    [
        {"scope": "x", "expires_on": "2027-01-01"},
        {"authorised_by": "a", "expires_on": "2027-01-01"},
        {"authorised_by": "a", "scope": "x"},
        {"authorised_by": "a", "scope": "x", "expires_on": "next March"},
        {"authorised_by": "a", "scope": "x", "expires_on": "2027-01-01", "excludes": "everything"},
    ],
)
def test_a_malformed_clearance_says_what_is_wrong(tmp_path: Path, broken: dict) -> None:
    """Treated as absent, with the reason said out loud: a malformed clearance is usually a typo in
    a date, and silently asking the rights question again leaves the person guessing why."""
    _write_clearance(tmp_path, broken)
    with pytest.raises(clearance_module.ClearanceError):
        clearance_module.load(tmp_path)


def test_an_expired_clearance_is_reported_as_expired(tmp_path: Path) -> None:
    _write_clearance(tmp_path, {**CLEARANCE, "expires_on": "2026-01-01"})
    found = clearance_module.load(tmp_path)
    assert found is not None
    assert not found.valid_on(date(2026, 11, 12))
    assert "EXPIRED" in found.summary(date(2026, 11, 12))
    assert found.expired_by(date(2026, 11, 12)) == 315


def test_the_clearance_record_omits_the_internal_policy(tmp_path: Path) -> None:
    """The excludes list is the organisation's own policy, not the register's business."""
    _write_clearance(tmp_path)
    record = clearance_module.load(tmp_path).as_record()
    assert "production customer records" not in record.model_dump_json()


def test_the_template_is_not_a_valid_clearance(tmp_path: Path) -> None:
    """An unfilled template must not silently count as permission."""
    clearance_module.write_template(tmp_path)
    with pytest.raises(clearance_module.ClearanceError):
        clearance_module.load(tmp_path)


def test_the_template_never_overwrites(tmp_path: Path) -> None:
    clearance_module.write_template(tmp_path)
    with pytest.raises(clearance_module.ClearanceError):
        clearance_module.write_template(tmp_path)


# -- the CLI --------------------------------------------------------------------------------


def test_offer_prints_the_decision_and_asks_nothing(entry: MisbehaviourReport, tmp_path: Path) -> None:
    path = tmp_path / "report.yaml"
    path.write_text(store.dump_yaml(_leaky(entry)), encoding="utf-8")
    result = CliRunner().invoke(
        report_cli, ["offer", str(path), "--register-root", "ifixai/register/examples"]
    )
    assert result.exit_code == 0, result.output
    assert "Agent failures like this aren't published anywhere today." in result.output
    assert "Contribute to the public catalogue?" not in result.output
    assert "Jane Okonkwo" not in result.output


def test_offer_json_is_what_a_harness_decides_with(entry: MisbehaviourReport, tmp_path: Path) -> None:
    import json

    path = tmp_path / "report.yaml"
    path.write_text(store.dump_yaml(_as_fresh_finding(entry)), encoding="utf-8")
    result = CliRunner().invoke(
        report_cli, ["offer", str(path), "--json", "--register-root", "ifixai/register/examples"]
    )
    payload = json.loads(result.output)
    assert payload["signature"] == entry.signature.digest
    assert payload["worth_offering"] is False  # already filed
    assert payload["validates"] is True


def test_clearance_show_reports_absence_without_crashing(tmp_path: Path) -> None:
    result = CliRunner().invoke(report_cli, ["clearance", "show", "--at", str(tmp_path)])
    assert result.exit_code == 1
    assert "asked about publication rights per submission" in result.output


def test_clearance_init_then_show(tmp_path: Path) -> None:
    created = CliRunner().invoke(report_cli, ["clearance", "init", "--at", str(tmp_path)])
    assert created.exit_code == 0
    assert (tmp_path / ".ifixai" / "contribution.yaml").exists()
    # An unfilled template is not permission, and `show` says so rather than claiming clearance.
    shown = CliRunner().invoke(report_cli, ["clearance", "show", "--at", str(tmp_path)])
    assert shown.exit_code != 0
