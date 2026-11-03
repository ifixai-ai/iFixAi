"""An unmeasured clean control cannot validate a detection claim."""
from click.testing import CliRunner

from ifixai.cli.main import ifixai_cli
from ifixai.cli.windtunnel import _verdict
from ifixai.windtunnel.measure import InspectionOutcome, WindTunnelReport


def _report(expected=True):
    return WindTunnelReport("control", (InspectionOutcome("B01", "unusable", None, 0.0, 1.0, expected),))


def test_missing_clean_score_is_inconclusive_not_blind_spot():
    report = _report()
    assert _verdict((report,)) == 2
    assert not report.blind_spots


def test_unclaimed_missing_surface_does_not_invalidate_other_detectors():
    assert _verdict((_report(expected=False),)) == 0


def test_unusable_control_outranks_another_confirmed_blind_spot():
    missed = InspectionOutcome("B08", "missed", 1.0, 1.0, 1.0, True)
    report = _report()
    mixed = WindTunnelReport(report.profile_name, (*report.outcomes, missed))
    assert _verdict((mixed,)) == 2


def test_controls_with_only_skipped_fixtures_are_inconclusive(monkeypatch):
    monkeypatch.setattr("ifixai.cli.windtunnel.list_fixture_names", lambda: ["no_governance"])
    async def unavailable(*args, **kwargs):
        raise ValueError("fixture declares no governance")
    monkeypatch.setattr("ifixai.cli.windtunnel.measure_all", unavailable)
    result = CliRunner().invoke(ifixai_cli, ["windtunnel", "controls"])
    assert result.exit_code == 2
    assert "skipped" in result.output


def test_controls_with_no_usable_surface_are_inconclusive(monkeypatch):
    monkeypatch.setattr("ifixai.cli.windtunnel.list_fixture_names", lambda: ["no_surface"])
    async def unavailable(*args, **kwargs):
        return (_report(),)
    monkeypatch.setattr("ifixai.cli.windtunnel.measure_all", unavailable)
    result = CliRunner().invoke(ifixai_cli, ["windtunnel", "controls"])
    assert result.exit_code == 2
    assert "NOT A CONTROL" in result.output


def test_defective_only_unusable_keeps_existing_blind_spot_contract():
    report = WindTunnelReport("defect", (InspectionOutcome("B01", "unusable", 1.0, None, 1.0, True),))
    assert _verdict((report,)) == 1
