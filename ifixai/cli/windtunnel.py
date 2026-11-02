"""Offline wind-tunnel measurement; no provider, credential or network path.

Exit codes follow the repository's conclusiveness discipline rather than a
pass/fail reading, because "the suite caught the defect" and "the suite could
not be asked" are different answers and only one of them is citable:

====  ===========================================================
0     Every expected detector caught its defect.
1     At least one expected detector missed it: a blind spot.
2     Inconclusive. The clean control did not pass, so no
      detection claim from this run means anything.
====  ===========================================================
"""

from __future__ import annotations

import asyncio

import click

from ifixai.core.fixture_loader import list_fixture_names
from ifixai.windtunnel.defects import (
    CATALOGUE,
    CLEAN,
    DefectKind,
    DefectProfile,
    leak_profiles,
)
from ifixai.windtunnel.evidence import (
    BaselineComparison,
    compare_to_baseline,
    create_bundle,
    load_bundle,
    write_bundle,
)
from ifixai.windtunnel.measure import WindTunnelReport, measure_all

#: Exit code per outcome, worst wins. 2 beats 1 because an unusable control
#: makes a blind spot unmeasurable rather than confirmed.
_INCONCLUSIVE = 2
_BLIND_SPOT = 1
_CLEAN = 0


def _verdict(
    reports: tuple[WindTunnelReport, ...],
    comparison: BaselineComparison | None = None,
) -> int:
    # A regression against a recorded baseline fails even when this run has no
    # blind spot of its own: the suite used to catch something and no longer
    # does, which is the one result a build should never go green on. It cannot
    # raise the code past 2, because an inconclusive control means the
    # comparison itself is not trustworthy.
    if any(report.false_positives for report in reports):
        return _INCONCLUSIVE
    if any(o.expected and o.clean_score is None for report in reports for o in report.outcomes):
        return _INCONCLUSIVE
    # An expected detector that never observed its defect leaves the profile's
    # claim untested. That is not a pass: the injector did not reach the
    # inspection, so this run says nothing about whether it would catch the
    # real thing. Inconclusive rather than a blind spot, because the gap is in
    # the measurement, not in the inspection.
    if any(report.unreached for report in reports):
        return _INCONCLUSIVE
    if comparison is not None and comparison.regressed:
        return _BLIND_SPOT
    if any(report.blind_spots for report in reports):
        return _BLIND_SPOT
    return _CLEAN


def _seal(
    profiles: tuple[DefectProfile, ...],
    reports: tuple[WindTunnelReport, ...],
    fixture: str,
    output: str | None,
    baseline: str | None,
) -> BaselineComparison | None:
    """Write the bundle if asked, and compare against a baseline if given."""
    bundle = create_bundle(tuple(zip(profiles, reports, strict=True)), fixture)
    if output:
        try:
            written = write_bundle(bundle, output)
        except (ValueError, OSError) as error:
            raise click.ClickException(str(error)) from None
        click.echo(f"\nEvidence: {written}  digest {bundle.integrity_digest[:16]}")

    if not baseline:
        return None
    try:
        recorded = load_bundle(baseline)
        comparison = compare_to_baseline(recorded, bundle)
    except (ValueError, OSError) as error:
        raise click.ClickException(str(error)) from None

    if comparison.identical:
        click.echo("\nBaseline: identical measurement (digests match).")
    for change in comparison.regressions:
        click.echo(
            f"\nREGRESSION {change.profile}/{change.test_id}: "
            f"{change.was} -> {change.now}"
        )
    for change in comparison.improvements:
        click.echo(
            f"\nImproved {change.profile}/{change.test_id}: "
            f"{change.was} -> {change.now}"
        )
    if comparison.unmatched:
        click.echo(
            "\nNot compared (present in only one bundle): "
            + ", ".join(comparison.unmatched)
        )
    return comparison


def _echo_report(report: WindTunnelReport, verbose: bool) -> None:
    click.echo(report.summary())
    rows = (
        report.outcomes
        if verbose
        else tuple(
            outcome
            for outcome in report.outcomes
            if outcome.expected
            or outcome.is_surprise
            or outcome.outcome == "false_positive"
        )
    )
    for outcome in rows:
        clean = "—" if outcome.clean_score is None else f"{outcome.clean_score:.2f}"
        defective = (
            "—" if outcome.defective_score is None else f"{outcome.defective_score:.2f}"
        )
        marker = "*" if outcome.expected else " "
        click.echo(
            f"  {marker}{outcome.test_id:<5}{outcome.outcome:<16}"
            f"clean={clean:<6}defective={defective:<6}"
            f"threshold={outcome.threshold:.2f}"
        )


def _echo_closing(reports: tuple[WindTunnelReport, ...]) -> None:
    false_positives = {o.test_id for r in reports for o in r.false_positives}
    if false_positives:
        click.echo(
            "\nInconclusive: the clean control failed "
            f"{', '.join(sorted(false_positives))} on this fixture. A detection "
            "rate measured against a control that does not pass says nothing "
            "about the suite."
        )
        return
    unavailable = {
        o.test_id for r in reports for o in r.outcomes
        if o.expected and o.clean_score is None
    }
    if unavailable:
        click.echo(
            f"\nInconclusive: the clean control produced no usable score for "
            f"{', '.join(sorted(unavailable))}. No paired detection can be claimed."
        )
        return
    unreached = {o.test_id for r in reports for o in r.unreached}
    if unreached:
        click.echo(
            f"\nNot exercised: {', '.join(sorted(unreached))} gathered "
            "identical evidence in both runs, so the defect never reached "
            "them. This run cannot say whether they would catch it; the gap "
            "is in the injector or in the profile's claim, not in the "
            "inspection."
        )
    blind = {o.test_id for r in reports for o in r.blind_spots}
    if blind:
        click.echo(
            f"\nBlind spots: {', '.join(sorted(blind))} observed the defect "
            "they exist to catch and did not move."
        )


def _run(coroutine) -> tuple[WindTunnelReport, ...]:
    try:
        return asyncio.run(coroutine)
    except ValueError as error:
        raise click.ClickException(str(error)) from None


@click.group()
def windtunnel() -> None:
    """Measure what the inspections detect, against seeded defects."""


@windtunnel.command("run")
@click.argument("profile", type=click.Choice(sorted(CATALOGUE)), metavar="PROFILE")
@click.option(
    "--fixture", default="default", show_default=True, help="Fixture to audit against."
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Show every inspection, not only the expected detectors.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False),
    help="Write sealed evidence here. The path must not exist.",
)
@click.option(
    "--baseline",
    type=click.Path(exists=True, dir_okay=False),
    help="Compare against this recorded bundle; a lost detection exits 1.",
)
def run_profile(
    profile: str, fixture: str, verbose: bool, output: str | None, baseline: str | None
) -> None:
    """Run one named defect profile and report what caught it."""
    profiles = (CATALOGUE[profile],)
    reports = _run(measure_all(profiles, fixture=fixture))
    for report in reports:
        _echo_report(report, verbose)
    _echo_closing(reports)
    comparison = _seal(profiles, reports, fixture, output, baseline)
    raise click.exceptions.Exit(_verdict(reports, comparison))


@windtunnel.command("sweep")
@click.option(
    "--fixture", default="default", show_default=True, help="Fixture to audit against."
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Show every inspection, not only the expected detectors.",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False),
    help="Write sealed evidence here. The path must not exist.",
)
@click.option(
    "--baseline",
    type=click.Path(exists=True, dir_okay=False),
    help="Compare against this recorded bundle; a lost detection exits 1.",
)
def sweep(
    fixture: str, verbose: bool, output: str | None, baseline: str | None
) -> None:
    """Run the whole catalogue against one fixture."""
    profiles = tuple(
        profile
        for profile in CATALOGUE.values()
        # PERMIT_DESTRUCTION needs the caller to name the destructive tools the
        # fixture declares, so it cannot be swept from a profile name alone.
        if profile.for_kind(DefectKind.PERMIT_DESTRUCTION) is None
    )
    reports = _run(measure_all(profiles, fixture=fixture))
    for report in reports:
        _echo_report(report, verbose)
    _echo_closing(reports)
    comparison = _seal(profiles, reports, fixture, output, baseline)
    raise click.exceptions.Exit(_verdict(reports, comparison))


@windtunnel.command("controls")
def controls() -> None:
    """Check the clean control on every bundled fixture.

    Run this before citing any detection rate. A fixture whose reference
    provider fails an inspection before a defect is injected cannot support a
    measurement, and which fixtures those are is not obvious from reading them.
    """
    failures = 0
    measured = 0
    for name in list_fixture_names():
        try:
            report = _run(measure_all((CLEAN,), fixture=name))[0]
        except click.ClickException as error:
            click.echo(f"{name:<24} skipped — {error.message}")
            continue
        measured += 1
        unusable = tuple(o.test_id for o in report.outcomes if o.outcome == "unusable")
        false_positives = tuple(o.test_id for o in report.false_positives)
        if not report.outcomes or len(unusable) == len(report.outcomes):
            failures += 1
            click.echo(f"{name:<24} NOT A CONTROL — no usable inspection surface")
        elif false_positives:
            failures += 1
            click.echo(f"{name:<24} NOT A CONTROL — fails {', '.join(false_positives)}")
        else:
            click.echo(
                f"{name:<24} clean"
                + (f" (no surface: {', '.join(unusable)})" if unusable else "")
            )
    raise click.exceptions.Exit(_INCONCLUSIVE if failures or not measured else _CLEAN)


@windtunnel.command("sensitivity")
@click.option(
    "--fixture", default="default", show_default=True, help="Fixture to audit against."
)
def sensitivity(fixture: str) -> None:
    """Trace a gate's sensitivity curve across partial leak rates.

    The mandatory minimums exist to make a single bad permission cell fatal, so
    the question worth answering is not whether a wholly broken layer is caught
    but how small a leak still trips the gate.
    """
    reports = _run(measure_all(leak_profiles(), fixture=fixture))
    for report in reports:
        _echo_report(report, verbose=False)
    raise click.exceptions.Exit(_verdict(reports))


__all__ = ["windtunnel"]
