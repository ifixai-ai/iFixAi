"""The human gate. One submission, one explicit yes, no way round it.

The constraint this module enforces outranks every adoption goal in the project: **no submission
leaves a machine without a person approving that specific submission.** Not a person who once
approved submissions in general; not a flag set in a config file; not an agent acting on a
standing instruction. The person who holds the data types the confirmation, having just been shown
what would be published.

How it is enforced, rather than merely intended:

* An :class:`Approval` cannot be constructed without a :class:`HumanWitness`, and a
  ``HumanWitness`` cannot be constructed without :func:`confirm`, which reads typed answers from an
  interactive stream. There is no non-interactive branch and no environment variable that supplies
  one.
* An approval is bound to the exact bytes it approved (``payload_digest``). Approve a report, edit
  one character, and the approval no longer matches — :func:`ifixai.register.submit.submit` refuses
  it. So an approval cannot be harvested from one submission and replayed on another.
* The two provenance flags are confirmed separately and by name. De-identification is something a
  tool can attempt; rights clearance is not, so the person says both out loud.
* ``ifixai/tests/register/test_human_gate.py`` asserts all of the above, and additionally greps
  this package for the shapes a future bypass would take (``--yes``, ``force``, an env var read in
  the submit path). A bypass added later fails that test.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, TextIO

from ifixai.register.spec import MisbehaviourReport

#: What the person must type. Not "y": a one-letter answer is what someone types to make a prompt
#: go away, and this prompt is the last thing standing between an audit fixture and 45,000 readers.
CONFIRMATION_WORD = "publish"


class ApprovalRequired(RuntimeError):
    """Raised when something tries to submit without an approval that matches what it is sending."""


class ApprovalDeclined(RuntimeError):
    """The person said no, or did not answer the confirmation exactly."""


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


PROMPTS = (
    (
        "de_identified",
        "You have read the redacted column above. Does it contain no real person, customer, account, "
        "hostname or secret from your systems? [y/N] ",
    ),
    (
        "rights_cleared",
        "Are you entitled to publish this? (For an audit-derived report: does your agreement with the "
        "audited party permit it?) [y/N] ",
    ),
)


def _ask(prompt: str, reader: Callable[[], str], writer: TextIO) -> str:
    writer.write(prompt)
    writer.flush()
    return reader().strip()


def confirm(
    report: MisbehaviourReport,
    granted_by: str,
    reader: Optional[Callable[[], str]] = None,
    writer: Optional[TextIO] = None,
) -> Approval:
    """Ask the person, in front of the diff, and return an approval or raise.

    ``reader`` and ``writer`` exist so the test suite can drive the prompts; the default reads the
    real console. They are parameters, not configuration: there is no way to pass a reader that
    answers on its own from outside a test, because the only caller that supplies one is the test
    suite, and an agent driving the CLI gets the real console and therefore a real person.
    """
    import sys

    stream = writer if writer is not None else sys.stderr
    read = reader if reader is not None else input
    answers: dict[str, str] = {}
    for key, prompt in PROMPTS:
        answer = _ask(prompt, read, stream)
        answers[key] = answer
        if answer.lower() not in ("y", "yes"):
            raise ApprovalDeclined(f"not confirmed: {key}. Nothing has been sent.")
    typed = _ask(
        f"Type {CONFIRMATION_WORD!r} to open a public pull request with the redacted report, or anything "
        "else to stop: ",
        read,
        stream,
    )
    answers["confirmation"] = typed
    if typed != CONFIRMATION_WORD:
        raise ApprovalDeclined("confirmation word not given. Nothing has been sent.")
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
    )


def require(approval: Optional[Approval], report: MisbehaviourReport) -> Approval:
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
    return {
        "payload_digest": approval.payload_digest,
        "granted_by": approval.granted_by,
        "granted_at": approval.granted_at.isoformat(),
        "de_identified_confirmed": approval.de_identified_confirmed,
        "rights_cleared_confirmed": approval.rights_cleared_confirmed,
    }
