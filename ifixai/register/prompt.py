"""The moment of discovery: de-identify first, then ask once.

A contribution path that begins with "go and write a report" collects nothing. The only moment
anybody will file is the moment the run comes back and the behaviour is on the screen, so this
module does everything that can be done without permission — builds the report, de-identifies it,
computes the signature, checks it against the register, works out whether a standing clearance
already covers the rights — and only then asks.

The order matters more than the convenience. **De-identification runs before the question, not
after it**, so the thing the person is agreeing to publish is the redacted thing, and the summary
of what was replaced is on the screen when they answer. Nothing touches the network before the
``y``, and `--show` renders the whole original-versus-redacted diff for anyone who wants to read it
all before deciding.

What is deliberately absent: any attempt to persuade. The context above the prompt is two factual
lines about a gap and its consequence (``approval.CONTEXT_LINES``). No count of other contributors,
no appeal to community, no urgency, and no framing of ``N`` as a loss — ``N`` is the default and a
perfectly normal answer. This is a security tool whose central promise is that de-identification
happens locally; a prompt that reads as marketing is a prompt that makes people doubt that promise.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TextIO

from ifixai.register import clearance as clearance_module
from ifixai.register import diff, proposal, scrub, store
from ifixai.register.approval import Approval, ApprovalDeclined, confirm, context_block
from ifixai.register.clearance import Clearance
from ifixai.register.scrub import ScrubResult
from ifixai.register.spec import MisbehaviourReport


@dataclass(frozen=True)
class Offer:
    """Everything prepared locally, before anybody is asked anything."""

    scrubbed: ScrubResult
    proposal: proposal.Proposal
    clearance: Clearance | None
    clearance_problem: str | None
    today: date

    @property
    def report(self) -> MisbehaviourReport:
        return self.scrubbed.report

    @property
    def known_signature(self) -> bool:
        return bool(self.proposal.duplicates)

    @property
    def pre_cleared(self) -> bool:
        return self.clearance is not None and self.clearance.valid_on(self.today)

    def headline(self) -> str:
        """The signature, the numbers and the redaction summary: what the decision rests on."""
        report = self.report
        lines = [
            f"Behaviour: {report.title}",
            f"Signature: {report.signature.family} / {report.signature.failure} / "
            f"{report.signature.trigger}  ({report.signature.digest})",
        ]
        for observed in report.observed:
            lines.append(
                f"  {observed.model.snapshot} via {observed.scaffold.harness} "
                f"{observed.scaffold.harness_version}: {observed.occurrences}/{observed.trials}"
                f"   permitted twin over-refused {observed.twin.over_refusals}/{observed.twin.trials}"
            )
            for rung in observed.gradient:
                lines.append(f"    {rung.variant:<20} {rung.occurrences}/{rung.trials}")
            if observed.scaffold.guardrails:
                active = ", ".join(guard.name for guard in observed.scaffold.guardrails)
                lines.append(f"    with guardrails active: {active}")
        if report.negative_results:
            for negative in report.negative_results:
                lines.append(
                    f"  did NOT show it: {negative.model.snapshot} (0/{negative.trials})"
                )
        lines.append("")
        lines.append(f"De-identified locally. {diff.summary(self.scrubbed.findings)}")
        if self.scrubbed.findings:
            kinds: dict[str, int] = {}
            for finding in self.scrubbed.findings:
                kinds[finding.kind] = kinds.get(finding.kind, 0) + 1
            for kind, count in sorted(kinds.items()):
                lines.append(f"  {kind.lower():<12} {count} replaced")
        if self.clearance_problem:
            lines.append(f"Clearance file: {self.clearance_problem}")
        elif self.clearance is not None:
            lines.append(self.clearance.summary(self.today))
        if self.known_signature:
            lines.append("")
            for duplicate in self.proposal.duplicates:
                lines.append(f"Already filed: {duplicate}")
        if not self.proposal.validation.ok:
            lines.append("")
            lines.append("This draft does not yet pass validation:")
            lines.append(self.proposal.validation.render())
        return "\n".join(lines)


def build(
    report: MisbehaviourReport,
    register_root: Path = Path(),
    project_root: Path = Path(),
    extra_names: Sequence[str] = (),
    today: date | None = None,
) -> Offer:
    """Do all of it locally: redact, validate, de-duplicate, read the standing clearance.

    No network, no prompt, no side effect outside reading files. Safe to call on every run, which is
    the point — the offer has to exist before anyone decides whether to make it.
    """
    day = today or date.today()
    scrubbed = scrub.scrub(report, tuple(extra_names))
    register = store.load_register(register_root)
    prepared = proposal.prepare(scrubbed.report, register.reports)
    found: Clearance | None = None
    problem: str | None = None
    try:
        found = clearance_module.load(project_root)
    except clearance_module.ClearanceError as error:
        problem = str(error)
    return Offer(
        scrubbed=scrubbed,
        proposal=prepared,
        clearance=found,
        clearance_problem=problem,
        today=day,
    )


def ask(
    offer: Offer,
    granted_by: str,
    writer: TextIO,
    reader: Callable[[], str] | None = None,
    show_diff: bool = False,
) -> Approval | None:
    """Show what was prepared and ask once. Returns None when the person declines.

    Declining is not an error and is reported as the ordinary outcome it is. The caller has sent
    nothing at this point and must not send anything if this returns None.
    """
    if show_diff:
        writer.write(diff.plain(offer.scrubbed) + "\n")
    writer.write(offer.headline() + "\n\n")
    try:
        return confirm(
            offer.report,
            granted_by=granted_by,
            clearance=offer.clearance,
            reader=reader,
            writer=writer,
            today=offer.today,
        )
    except ApprovalDeclined as declined:
        writer.write(f"{declined}\n")
        return None


def should_offer(offer: Offer) -> bool:
    """Whether to interrupt at all.

    Only for a behaviour the register does not already hold: a prompt that fires on every run is a
    prompt people learn to ignore, and a duplicate filing costs a curator more than it gains.
    """
    return not offer.known_signature


__all__ = ["Offer", "ask", "build", "context_block", "should_offer"]
