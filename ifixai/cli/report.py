"""``ifixai report``: file a reproducible misbehaviour report, in one step, safely.

The order is fixed and the same every time, because it is the order that keeps the register
publishable: de-identify locally, show the person the original beside the redaction, take an
explicit per-submission yes, then open a pull request. There is no flag that skips a step and no
configuration that takes the human out of it.

``draft`` builds a report from a run, ``validate`` checks one, ``signature`` says whether the
register already knows a behaviour, ``review`` shows the side-by-side without sending, ``submit``
does the whole path, and ``mcp`` runs the prepare-only server an editor's agent talks to.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import click
import yaml

from ifixai.register import clearance as clearance_module
from ifixai.register import diff as diff_module
from ifixai.register import mcp as mcp_module
from ifixai.register import (
    prompt,
    proposal,
    scrub,
    signature,
    store,
    submit,
    validator,
)
from ifixai.register.approval import ApprovalRequired
from ifixai.register.draft import unfinished
from ifixai.register.spec import SPEC_VERSION, BehaviourSignature, MisbehaviourReport
from ifixai.register.submit import SubmissionError


def _read(path: str) -> MisbehaviourReport:
    try:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise click.ClickException(f"cannot read {path}: {type(error).__name__}") from None
    result = validator.validate_payload(payload)
    if result.report is None:
        raise click.ClickException(f"{path} does not match misbehaviour report spec {SPEC_VERSION}:\n{result.render()}")
    return result.report


@click.group()
def report() -> None:
    """File a reproducible report of AI-agent misbehaviour to the public register.

    The register is ifixai-ai/agent-misbehavior-registry. Everything here runs locally and needs no
    provider key; only `submit` touches the network, and only after you have said yes to that
    submission.
    """


@report.command("validate")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--register-root", default=".", type=click.Path(file_okay=False), help="Checkout holding register/.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output, for CI.")
def validate_cmd(paths: tuple[str, ...], register_root: str, as_json: bool) -> None:
    """Check reports against the spec, and flag duplicate behaviour signatures."""
    register = store.load_register(Path(register_root))
    findings = []
    failed = False
    for path in paths:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        result = validator.validate_payload(payload)
        problems = [str(problem) for problem in result.problems]
        duplicates: list = []
        placeholders: list = []
        if result.report is not None:
            # A report filed from this same file must not count as its own duplicate.
            others = [
                other
                for other in register.reports
                if (other.id or other.candidate_ref) != (result.report.id or result.report.candidate_ref)
            ]
            duplicates = [str(item) for item in validator.find_duplicates(result.report, others)]
            placeholders = list(unfinished(result.report))
            if placeholders:
                problems.append(
                    "[draft] "
                    + ", ".join(placeholders[:6])
                    + ": still carries the drafter's TODO placeholder; a submission has to be finished"
                )
        failed = failed or bool(problems)
        findings.append(
            {"path": path, "ok": not problems, "problems": problems, "duplicates": duplicates}
        )
    if as_json:
        click.echo(json.dumps({"spec_version": SPEC_VERSION, "results": findings}, indent=2))
    else:
        for finding in findings:
            mark = "ok  " if finding["ok"] else "FAIL"
            click.echo(f"{mark} {finding['path']}")
            for problem in finding["problems"]:
                click.echo(f"       - {problem}")
            for duplicate in finding["duplicates"]:
                click.echo(f"       ~ possible duplicate: {duplicate}")
    raise click.exceptions.Exit(1 if failed else 0)


@report.command("signature")
@click.option("--family", required=True, help="The task family, e.g. authority_boundary.")
@click.option("--failure", required=True, help="The failed check, e.g. authority.violation.")
@click.option("--trigger", required=True, help="The condition present in every failing trial.")
@click.option("--register-root", default=".", type=click.Path(file_okay=False))
def signature_cmd(family: str, failure: str, trigger: str, register_root: str) -> None:
    """The behaviour signature, and whether the register already holds it."""
    sig = BehaviourSignature(family=family, failure=failure, trigger=trigger)
    digest = signature.digest(sig)
    click.echo(f"signature {digest}  ({family} / {failure} / {trigger})")
    register = store.load_register(Path(register_root))
    hits = [
        f"{other.id or other.candidate_ref} ({other.state.value})"
        for other in register.reports
        if signature.digest(other.signature) == digest
    ]
    if hits:
        click.echo("already filed as: " + ", ".join(hits))
    else:
        click.echo("no report in the register has this signature")


@report.command("review")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--name", "names", multiple=True, help="A real name to strike from free text too. Repeatable.")
@click.option("--plain", is_flag=True, help="Plain text instead of a table, for piping.")
def review_cmd(path: str, names: tuple[str, ...], plain: bool) -> None:
    """De-identify locally and show the original beside what would be published. Sends nothing."""
    result = scrub.scrub(_read(path), tuple(names))
    if plain:
        click.echo(diff_module.plain(result))
    else:
        diff_module.render(result)
    click.echo("")
    click.echo("Nothing has been sent. `ifixai report submit` asks for your approval before it does.")


@report.command("submit")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--name", "names", multiple=True, help="A real name to strike from free text too. Repeatable.")
@click.option("--as", "granted_by", required=True, help="Who is approving this submission, for the audit trail.")
@click.option(
    "--checkout",
    default=".",
    type=click.Path(file_okay=False),
    help="A clone of the register repository, where the branch is made.",
)
@click.option("--repo", default=proposal.DEFAULT_REGISTER_REPO, show_default=True, help="Where the PR is opened.")
@click.option("--show", "show_diff", is_flag=True, help="Print the whole original-vs-redacted diff first.")
@click.option("--dry-run", is_flag=True, help="Do everything except push and open the pull request.")
def submit_cmd(
    path: str,
    names: tuple[str, ...],
    granted_by: str,
    checkout: str,
    repo: str,
    show_diff: bool,
    dry_run: bool,
) -> None:
    """De-identify, show what would be published, ask once, then open the pull request."""
    root = Path(checkout)
    offer = prompt.build(_read(path), register_root=root, project_root=Path(), extra_names=names)
    left = unfinished(offer.report)
    if left:
        raise click.ClickException(
            "this draft is unfinished; these fields still carry the TODO placeholder:\n  " + "\n  ".join(left)
        )
    if offer.proposal.blocked_reason is not None:
        raise click.ClickException(offer.proposal.blocked_reason)
    click.echo(f"This would open a pull request against {repo}, adding {offer.proposal.relative_path}.", err=True)
    approval = prompt.ask(offer, granted_by=granted_by, writer=click.get_text_stream("stderr"), show_diff=show_diff)
    if approval is None:
        raise click.exceptions.Exit(0)
    try:
        sent = submit.submit(offer.proposal, approval, checkout=root, repo=repo, dry_run=dry_run)
    except (SubmissionError, ApprovalRequired) as error:
        raise click.ClickException(str(error)) from None
    if dry_run:
        click.echo(f"Committed to branch {sent.branch}. Nothing pushed (--dry-run).")
    else:
        click.echo(f"Filed: {sent.pull_request_url or sent.branch}")


@report.command("offer")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--name", "names", multiple=True, help="A real name to strike from free text too. Repeatable.")
@click.option("--register-root", default=".", type=click.Path(file_okay=False), help="A clone of the register.")
@click.option("--show", "show_diff", is_flag=True, help="Print the whole original-vs-redacted diff first.")
@click.option("--json", "as_json", is_flag=True, help="What was prepared, for a harness to decide with.")
def offer_cmd(path: str, names: tuple[str, ...], register_root: str, show_diff: bool, as_json: bool) -> None:
    """What the prompt at the moment of discovery shows, without asking.

    This is what a run calls when it has surfaced something: everything is prepared and
    de-identified locally, and nothing is asked and nothing is sent. `ifixai report submit` is the
    same preparation followed by the one question.
    """
    offer = prompt.build(_read(path), register_root=Path(register_root), extra_names=names)
    if as_json:
        click.echo(
            json.dumps(
                {
                    "signature": offer.report.signature.digest,
                    "already_filed": [str(duplicate) for duplicate in offer.proposal.duplicates],
                    "worth_offering": prompt.should_offer(offer),
                    "redactions": len(offer.scrubbed.findings),
                    "validates": offer.proposal.validation.ok,
                    "rights_pre_cleared": offer.pre_cleared,
                },
                indent=2,
            )
        )
        return
    if show_diff:
        click.echo(diff_module.plain(offer.scrubbed))
    click.echo(offer.headline())
    click.echo("")
    click.echo(prompt.context_block())
    click.echo("")
    if prompt.should_offer(offer):
        click.echo(f'Nothing asked and nothing sent. To file it: ifixai report submit {path} --as "your name"')
    else:
        click.echo("This behaviour is already in the register, so a run would not interrupt to offer it.")


@report.group("clearance")
def clearance_group() -> None:
    """Your organisation's standing permission to publish findings."""


@clearance_group.command("init")
@click.option("--at", "root", default=".", type=click.Path(file_okay=False), help="The repository to write it in.")
def clearance_init(root: str) -> None:
    """Write the clearance template for somebody who can authorise it to fill in."""
    try:
        written = clearance_module.write_template(Path(root))
    except clearance_module.ClearanceError as error:
        raise click.ClickException(str(error)) from None
    click.echo(f"Wrote {written}. Have somebody who can actually grant it fill it in, then commit it.")
    click.echo("With a valid clearance, filing a report is one confirmation instead of two.")


@clearance_group.command("show")
@click.option("--at", "root", default=".", type=click.Path(file_okay=False))
def clearance_show(root: str) -> None:
    """Whether a standing clearance is present, and whether it is still valid."""
    try:
        found = clearance_module.load(Path(root))
    except clearance_module.ClearanceError as error:
        raise click.ClickException(str(error)) from None
    if found is None:
        click.echo(
            f"No {clearance_module.DEFAULT_PATH} here. You will be asked about publication rights per "
            "submission. `ifixai report clearance init` writes a template."
        )
        raise click.exceptions.Exit(1)
    click.echo(found.summary(date.today()))
    if found.excludes:
        click.echo("Excludes:")
        for item in found.excludes:
            click.echo(f"  - {item}")
    raise click.exceptions.Exit(0 if found.valid_on(date.today()) else 1)


@report.command("mcp")
def mcp_cmd() -> None:
    """Run the prepare-only MCP server an editor's agent talks to (stdio JSON-RPC)."""
    mcp_module.serve()
