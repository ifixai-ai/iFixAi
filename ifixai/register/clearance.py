"""Standing rights clearance, so filing a report is one keystroke and still auditable.

Two facts pull against each other. De-identification is machine-checkable, and this tool does it.
Rights clearance is not: only the organisation that owns the audited system can say whether its
agreements permit publishing a finding derived from it, and no amount of code will establish that.

Asking per submission is the correct answer and the wrong product. A developer who has just watched
their agent refund a disputed charge without authorisation will answer one question. They will not
go and find out whether their contract permits publication, and so they will file nothing, and the
register stays empty of exactly the findings that matter most.

So the question is asked once, by the person who can answer it, in a file committed to the
organisation's own repository:

.. code-block:: yaml

    # .ifixai/contribution.yaml
    version: "1"
    organisation: Acme Health Partners
    authorised_by: R. Mbeki, Head of Platform Security
    scope: Synthetic and de-identified agent misbehaviour findings from our own staging systems.
    expires_on: 2027-03-31
    excludes:
      - anything derived from production customer records

Committed, so it is reviewable, diffable and revocable by the same process as any other policy in
the repository; expiring, so a clearance granted by someone who has since left does not authorise
submissions forever; and recorded on the submission, so there is a trail saying who permitted what.

What this file does **not** do is remove the human from the loop. It answers the rights question in
advance. The person still sees what would be published and still says yes — once — and a submission
with no valid pre-clearance simply asks the rights question too. The gate itself is in
:mod:`ifixai.register.approval` and nothing here can satisfy it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from ifixai.register.spec import PreClearance

#: Where an organisation records its standing clearance. Matches the project's existing use of
#: ``.ifixai/`` for committed, reviewable configuration.
DEFAULT_PATH = Path(".ifixai") / "contribution.yaml"

TEMPLATE = """\
# Standing clearance for filing agent misbehaviour reports to the public register.
#
# Committed on purpose: this is a policy statement about what your organisation permits to be
# published, so it should be reviewed like one. Delete it to revoke it.
#
# With a valid clearance here, filing a report is one confirmation: you still see exactly what
# would be published and still say yes, but you are not asked about rights every time. Without it,
# you are asked about rights per submission, which is the same question asked less conveniently.
version: "1"
organisation: ""
authorised_by: ""        # a named person who can actually grant this, and their role
scope: ""                # what this covers, in your words
expires_on: ""           # YYYY-MM-DD. Keep it short enough that somebody re-reads it
excludes: []             # anything this clearance explicitly does not cover
"""


class ClearanceError(ValueError):
    """The clearance file is present but unusable. Treated as absent, with the reason said out loud
    rather than silently falling back: a malformed clearance is usually a typo in a date, and
    quietly asking the rights question again would leave the person guessing why."""


@dataclass(frozen=True)
class Clearance:
    """A parsed clearance, and whether it covers a submission today."""

    organisation: str
    authorised_by: str
    scope: str
    expires_on: date
    excludes: tuple[str, ...]
    source_file: str

    def valid_on(self, day: date) -> bool:
        return day <= self.expires_on

    def expired_by(self, day: date) -> int:
        return max(0, (day - self.expires_on).days)

    def as_record(self) -> PreClearance:
        """What goes on the submission: who authorised it, for what, until when. Never the
        ``excludes`` list, which is the organisation's internal policy rather than the register's
        business."""
        return PreClearance(
            authorised_by=self.authorised_by,
            organisation=self.organisation,
            scope=self.scope,
            expires_on=self.expires_on,
            source_file=self.source_file,
        )

    def summary(self, today: date) -> str:
        if self.valid_on(today):
            return (
                f"Rights pre-cleared by {self.authorised_by}"
                + (f" ({self.organisation})" if self.organisation else "")
                + f", until {self.expires_on}."
            )
        days = self.expired_by(today)
        return (
            f"Rights clearance from {self.authorised_by} EXPIRED {days} day(s) ago "
            f"({self.expires_on}); you will be asked about rights for this submission."
        )


def load(root: Path = Path(), path: Path = DEFAULT_PATH) -> Clearance | None:
    """The clearance in ``root``, or None when there is none.

    A missing file is not an error: most people will not have one, and the flow works without it.
    """
    target = root / path
    if not target.is_file():
        return None
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ClearanceError(f"{target} cannot be read: {type(error).__name__}") from None
    if not isinstance(payload, dict):
        raise ClearanceError(f"{target} should be a mapping of settings")
    missing = [key for key in ("authorised_by", "scope", "expires_on") if not str(payload.get(key, "")).strip()]
    if missing:
        raise ClearanceError(
            f"{target} is missing {', '.join(missing)}. A clearance needs a named person who granted it, "
            "what it covers, and when it stops being true."
        )
    expires = payload["expires_on"]
    if isinstance(expires, str):
        try:
            expires = date.fromisoformat(expires.strip())
        except ValueError:
            raise ClearanceError(f"{target}: expires_on should be YYYY-MM-DD, not {expires!r}") from None
    if not isinstance(expires, date):
        raise ClearanceError(f"{target}: expires_on should be a date")
    excludes = payload.get("excludes") or []
    if not isinstance(excludes, list):
        raise ClearanceError(f"{target}: excludes should be a list")
    return Clearance(
        organisation=str(payload.get("organisation", "")).strip(),
        authorised_by=str(payload["authorised_by"]).strip(),
        scope=str(payload["scope"]).strip(),
        expires_on=expires,
        excludes=tuple(str(item) for item in excludes),
        source_file=str(path.as_posix()),
    )


def write_template(root: Path = Path(), path: Path = DEFAULT_PATH) -> Path:
    """Write the template for somebody to fill in. Never overwrites an existing clearance."""
    target = root / path
    if target.exists():
        raise ClearanceError(f"{target} already exists; edit it rather than replacing it")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE, encoding="utf-8")
    return target
