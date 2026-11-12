"""The human gate. One submission, one explicit yes, no way round it.

The constraint this module enforces outranks every adoption goal in the project: **no submission
leaves a machine without a person approving that specific submission.** Not a person who once
approved submissions in general; not a flag in a config file; not an agent acting on a standing
instruction.

It is also, deliberately, **one** yes. The person has just been shown what would be published and a
summary of what was redacted; asking them to type a magic word after that would not make them read
it more carefully, it would train them to clear prompts. So the gate is a single ``[y/N]``
defaulting to No, asked under the redaction summary, plus a second question about publication
rights only when no standing clearance has already answered it
(:mod:`ifixai.register.clearance`).

How it is enforced, rather than merely intended:

* An :class:`Approval` cannot be constructed without a :class:`HumanWitness`, and a
  ``HumanWitness`` cannot be constructed without :func:`confirm`, which reads typed answers from an
  interactive stream. There is no non-interactive branch and no environment variable that supplies
  one. A standing clearance can answer the *rights* question in advance; nothing can answer the yes.
* An approval is bound to the exact bytes it approved (``payload_digest``). Approve a report, edit
  one character, and the approval no longer matches — :func:`ifixai.register.submit.submit` refuses
  it. So an approval cannot be harvested from one submission and replayed on another.
* ``ifixai/tests/register/test_human_gate.py`` asserts all of the above, and additionally greps
  this package for the shapes a future bypass would take (``--yes``, ``force``, an env var read in
  the submit path). A bypass added later fails that test.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:  # pragma: no cover - imported only for the annotation
    from ifixai.register.clearance import Clearance

from ifixai.register.spec import MisbehaviourReport, PreClearance

#: The one question. ``N`` is the default, and a decline is a normal answer: a prompt that defaults
#: to yes is a prompt people clear without reading, and a prompt that treats "no" as a loss is a
#: prompt that makes people distrust everything else the tool says it does quietly.
CONTRIBUTE_PROMPT = "Contribute to the public catalogue? [y/N] "

#: Asked only when there is no valid standing clearance (``ifixai.register.clearance``). Rights are
#: not machine-checkable, so somebody has to answer this; an organisation that has answered it once
#: in a committed file is not asked again.
RIGHTS_PROMPT = (
    "Are you entitled to publish this? (For a finding from an audited system: does your agreement "
    "with that party permit it?) [y/N] "
)

#: Printed immediately above the question. Factual: the gap, and the consequence of closing it.
#: Deliberately not persuasive — these are engineers running a security tool, and anything that
#: reads as a pitch makes them wonder what else the tool does quietly. That suspicion would land on
#: the de-identification promise, which cannot afford any.
CONTEXT_LINES = (
    "Agent failures like this aren't published anywhere today.",
    "Contributing makes it reproducible, so every team can test for it.",
)

AFFIRMATIVE = ("y", "yes")


class ApprovalRequired(RuntimeError):
    """Raised when something tries to submit without an approval that matches what it is sending."""


class ApprovalDeclined(RuntimeError):
    """The person said no, or did not answer affirmatively. Not an error state: declining is the
    default, and the caller reports it as a normal outcome."""


def payload_digest(report: MisbehaviourReport) -> str:
    """A stable digest of everything that would be published.

    Canonical JSON over the whole model, so any edit anywhere — a record field, a caveat, a
    trailing space in the request text — changes the digest and invalidates the approval.
    """
    payload = report.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HumanWitness:
    """Proof that a person was asked and answered, in this process, for this payload.

    Only :func:`confirm` builds one. It carries no authority of its own: it is the token
    :class:`Approval` demands, and it exists so that "a human said yes" is a thing the type system
    can require rather than a comment somebody can ignore.
    """

    digest: str
    answered_at: datetime
    answers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Approval:
    """A person's yes to publishing one exact payload."""

    witness: HumanWitness
    payload_digest: str
    de_identified_confirmed: bool
    rights_cleared_confirmed: bool
    granted_by: str
    granted_at: datetime
    #: The standing clearance the rights half rested on, when there was one. Recorded so the
    #: submission can say who authorised it rather than only that somebody did.
    pre_clearance: PreClearance | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.witness, HumanWitness):
            raise ApprovalRequired("an approval needs a HumanWitness from confirm(); nothing else builds one")
        if self.witness.digest != self.payload_digest:
            raise ApprovalRequired("the witness approved different bytes than this approval claims")
        if not (self.de_identified_confirmed and self.rights_cleared_confirmed):
            raise ApprovalDeclined(
                "both the de-identified and the rights-cleared flags must be confirmed before a submission is made"
            )
        if not self.granted_by.strip():
            raise ApprovalDeclined("an approval records who gave it")

    def covers(self, report: MisbehaviourReport) -> bool:
        """Whether this approval is for exactly this report."""
        return self.payload_digest == payload_digest(report)


def _ask(prompt: str, reader: Callable[[], str], writer: TextIO) -> str:
    writer.write(prompt)
    writer.flush()
    return reader().strip()


def context_block() -> str:
    return "\n".join(CONTEXT_LINES)


def confirm(
    report: MisbehaviourReport,
    granted_by: str,
    clearance: Clearance | None = None,
    reader: Callable[[], str] | None = None,
    writer: TextIO | None = None,
    today: date | None = None,
) -> Approval:
    """Ask the person, in front of the redaction, and return an approval or raise.

    One question when a valid standing clearance has already answered the rights question; two when
    it has not. Never fewer than one, and the one is never implied by a flag, a file or an agent:

    * The de-identified half is carried by the single yes **because the redaction summary is on the
      screen directly above it** — that is what the person is agreeing to, and an extra artificial
      step after it would only train people to clear prompts.
    * The rights half comes from the standing clearance where there is one, and is asked outright
      where there is not.

    ``reader`` and ``writer`` exist so the test suite can drive the prompts; the default reads the
    real console. They are parameters, not configuration: nothing outside a test supplies them, so
    an agent driving the CLI gets the real console and therefore a real person.
    """
    import sys

    stream = writer if writer is not None else sys.stderr
    read = reader if reader is not None else input
    day = today or date.today()
    answers: dict[str, str] = {}

    stream.write(context_block() + "\n")
    answer = _ask(CONTRIBUTE_PROMPT, read, stream)
    answers["contribute"] = answer
    if answer.lower() not in AFFIRMATIVE:
        raise ApprovalDeclined("Not contributed. Nothing has been sent.")

    pre_clearance = None
    if clearance is not None and clearance.valid_on(day):
        pre_clearance = clearance.as_record()
        answers["rights"] = f"pre-cleared by {clearance.authorised_by} until {clearance.expires_on}"
    else:
        rights = _ask(RIGHTS_PROMPT, read, stream)
        answers["rights"] = rights
        if rights.lower() not in AFFIRMATIVE:
            raise ApprovalDeclined("Rights not confirmed. Nothing has been sent.")

    digest = payload_digest(report)
    now = datetime.now(timezone.utc)
    witness = HumanWitness(digest=digest, answered_at=now, answers=answers)
    return Approval(
        witness=witness,
        payload_digest=digest,
        de_identified_confirmed=True,
        rights_cleared_confirmed=True,
        granted_by=granted_by,
        granted_at=now,
        pre_clearance=pre_clearance,
    )


def require(approval: Approval | None, report: MisbehaviourReport) -> Approval:
    """The check every sending path goes through. Call it before anything touches the network."""
    if approval is None:
        raise ApprovalRequired(
            "no human approval for this submission. Run `ifixai report submit`, read the side-by-side, and "
            "confirm; an agent cannot approve on your behalf."
        )
    if not approval.covers(report):
        raise ApprovalRequired(
            "the approval does not match what is about to be sent: the report changed after it was approved. "
            "Review the diff again and confirm the new version."
        )
    return approval


def record(approval: Approval) -> dict[str, Any]:
    """What goes into the submission's audit trail. Never the answers themselves, which would be a
    transcript of somebody's console, and never a reusable token."""
    entry: dict[str, Any] = {
        "payload_digest": approval.payload_digest,
        "granted_by": approval.granted_by,
        "granted_at": approval.granted_at.isoformat(),
        "de_identified_confirmed": approval.de_identified_confirmed,
        "rights_cleared_confirmed": approval.rights_cleared_confirmed,
    }
    if approval.pre_clearance is not None:
        entry["pre_clearance"] = {
            "authorised_by": approval.pre_clearance.authorised_by,
            "organisation": approval.pre_clearance.organisation,
            "expires_on": approval.pre_clearance.expires_on.isoformat(),
            "source_file": approval.pre_clearance.source_file,
        }
    return entry
