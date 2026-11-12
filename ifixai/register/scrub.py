"""Local de-identification. Nothing leaves the machine until this has run and a human has looked.

The register is public and permanent, and the people who have the most interesting findings are
the people auditing production systems. One leaked customer record would end the register, so the
order of operations is fixed and not configurable: scrub locally, show the human the original and
the redaction side by side, take an explicit yes, and only then offer to publish.

What this module is, and is not: a deterministic, auditable redactor over a report's values. It
replaces what it recognises with stable placeholders, so a record referenced in three places stays
one record and the report still validates afterwards. It is not a classifier and makes no claim to
find everything — which is exactly why the human sees the diff rather than a summary. Where it is
unsure it redacts, because a false positive costs a reader one confusing placeholder and a false
negative costs somebody their data.

Every rule here has a test in ``ifixai/tests/register/test_scrubber_nasties.py`` built from the
kinds of thing that actually appear in audit fixtures: names, work emails, phone numbers in six
formats, account and record ids, internal hostnames, live-looking API keys, and free-text notes
with a person buried mid-sentence.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern
from typing import Any, Optional

from ifixai.register.spec import MisbehaviourReport

#: Field names whose value is a person, wherever they appear in the world or the identities. These
#: are harvested first so the literal name can then be struck from free text as well.
#:
#: ``name`` is in the list but treated more carefully than the rest (see ``_is_a_person_value``): it
#: is the most generic key there is, and a queue called "supervisor review" is not a person.
PERSON_FIELD_HINTS = (
    "name",
    "full_name",
    "display_name",
    "customer",
    "customer_name",
    "contact",
    "contact_name",
    "owner",
    "owner_name",
    "requester",
    "assignee",
    "employee",
    "manager",
    "author",
)
#: Field names whose value is an organisation. Kept apart from people: a company name is not
#: personal data, but in an audit-derived report it identifies the customer, so it goes too.
ORG_FIELD_HINTS = ("company", "company_name", "organisation", "organization", "employer", "vendor_name", "client")

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Phone numbers vary too much by country for a shape-by-shape list: "+44 20 7946 0958",
# "(415) 555-0132" and "07700 900461" group their digits three different ways. So the pattern is a
# loose run of digits and the usual punctuation, and `_looks_like_phone` decides on the digit count,
# which is the part that is actually stable (7 to 15, per E.164).
_PHONE = re.compile(r"(?<![\w-])\+?\d[\d\s().\-]{5,20}\d(?![\w-])")
_PHONE_DIGITS = re.compile(r"\d")
_PHONE_GROUPS = re.compile(r"\d+")
#: Dates and year-numbered identifiers have as many digits as a phone number and are not one.
#: ``2026-11-06`` and the ``2026-0101`` inside ``IFX-2026-0101`` both used to come out as phone
#: numbers, which is how a redactor ends up mangling the register's own citations.
_DATE_LIKE = re.compile(r"^\d{4}[-/.]\d{1,4}([-/.]\d{1,4})?$")
# Card-like and account-like digit runs. The spaced/hyphenated card form first, then bare runs.
_CARD = re.compile(r"(?<![\w-])(?:\d{4}[ \-]){3}\d{4}(?![\w-])")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
# Labelled identifiers. The label is matched case-sensitively and in upper case on purpose: a
# case-insensitive ``ACC`` happily matches the word "accepted", and a redactor that rewrites the
# report's own vocabulary is worse than one that misses a number.
_ACCOUNT_LABELLED = re.compile(
    r"\b(?:ACCT|ACCOUNT|ACC|CUST|CUSTOMER|POL|POLICY|MBR|MEMBER|SSN|NI|TAX)[-_ ]?([A-Z0-9]{4,})\b"
)
_HAS_DIGIT = re.compile(r"\d")
_SSN = re.compile(r"(?<![\w-])\d{3}-\d{2}-\d{4}(?![\w-])")
# Internal hosts: a private TLD, or a host under one of the usual internal suffixes.
_INTERNAL_HOST = re.compile(
    r"\b(?:[A-Za-z0-9][A-Za-z0-9-]*\.)+(?:internal|corp|intranet|local|lan|localdomain|home|private|test)\b",
    re.IGNORECASE,
)
_PRIVATE_IP = re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
_URL_HOST = re.compile(r"\bhttps?://([A-Za-z0-9.-]+)")
_HONORIFIC = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Sir|Dame)\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
#: Two adjacent capitalised words inside somebody's data: "Jane Okonkwo", "Acme Health". Applied
#: only to free text under `world` and `identity` (see `walk`), never to the report's own prose,
#: where "Issue Refund" and "Authority Boundary" are the vocabulary rather than anybody's name. It
#: has false positives by design: a reader loses one confusing placeholder, and the alternative is
#: somebody's name in a public register because no structured field happened to hold it too.
_LIKELY_PERSON = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,}){1,2})\b")

#: Secret-shaped tokens. The provider list is the authority for keys this project itself issues or
#: consumes; these are the shapes that turn up pasted into a free-text note.
_KEY_RULES: tuple[tuple[Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w-])sk-ant-[A-Za-z0-9_-]{16,}"), "API_KEY"),
    (re.compile(r"(?<![\w-])sk-(?:proj-)?[A-Za-z0-9_-]{16,}"), "API_KEY"),
    (re.compile(r"(?<![\w-])sk-or-[A-Za-z0-9_-]{16,}"), "API_KEY"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS_KEY_ID"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "AWS_KEY_ID"),
    (re.compile(r"\bAIzaSy[0-9A-Za-z_-]{30,}"), "API_KEY"),
    (re.compile(r"(?<![\w-])hf_[A-Za-z0-9]{16,}"), "API_KEY"),
    (re.compile(r"(?<![\w-])gh[pousr]_[A-Za-z0-9]{20,}"), "TOKEN"),
    (re.compile(r"(?<![\w-])xox[baprs]-[A-Za-z0-9-]{10,}"), "TOKEN"),
    (re.compile(r"(?<![\w-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "JWT"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "PRIVATE_KEY"),
)

#: The format's own vocabulary. These values are never rewritten: they are enum members, derived
#: digests, dates and identifiers belonging to the report rather than to anybody's data, and a
#: redactor that touches them produces a file that no longer parses. (``state: accepted`` really did
#: come out as ``accACCT_0001`` before this list existed.)
NEVER_SCRUB_KEYS = frozenset({
    "spec_version", "state", "id", "candidate_ref", "rejection_reason",
    "predicate", "source", "source_kind", "combination", "identity_source", "status",
    "risk", "severity", "digest", "image_digest", "score",
    "created_on", "updated_on", "observed_on", "calibrated_on", "last_verified_on",
    "family", "failure", "trigger", "runtime", "version", "task_id",
    "model", "model_version", "harness", "harness_version",
})

#: Where a value may be a person or an organisation. Only the world and the identities hold
#: anybody's data; a ``name`` under ``tools`` is a tool's name ("Issue refund") and a ``name`` under
#: ``expected`` is a condition's, and striking those from the free text would shred the report.
PERSONAL_PATH_PREFIXES = ("world", "identity")

#: Values that are already placeholders. Re-redacting them would churn the diff for no gain.
_PLACEHOLDER = re.compile(r"^(?:PERSON|ORG|REC|ACCT|HOST|EMAIL|PHONE|CARD|IBAN|SSN|API_KEY|AWS_KEY_ID|TOKEN|JWT|PRIVATE_KEY|IP)_\d+$")


def _looks_like_phone(candidate: str) -> bool:
    """A digit run is a phone number when it has between 7 and 15 digits.

    Below seven it is a quantity, an amount or a year; above fifteen it is an identifier, and the
    account and card rules have already had their go at it. The guard runs after the pattern rather
    than inside it so the pattern can stay loose about how the digits are grouped.
    """
    text = candidate.strip()
    if _DATE_LIKE.match(text):
        return False
    digits = len(_PHONE_DIGITS.findall(text))
    if not 7 <= digits <= 15:
        return False
    groups = _PHONE_GROUPS.findall(text)
    # A phone number is written as a country code, as three or more groups, or as one long run.
    return text.startswith("+") or len(groups) >= 3 or digits >= 10


#: Hints specific enough that whatever is in them is a person, however it is written.
EXPLICIT_PERSON_HINTS = frozenset(PERSON_FIELD_HINTS) - {"name"}


def _is_a_person_value(key: str, value: str) -> bool:
    """Whether a value in a person-ish field really is a person's name.

    An explicit key (``customer_name``, ``display_name``, ``contact``) is taken at its word. A bare
    ``name`` has to look like a name — capitalised, and not made entirely of ordinary words — because
    ``{type: queue, name: supervisor review}`` is a queue.
    """
    text = value.strip()
    if not text:
        return False
    if key in EXPLICIT_PERSON_HINTS:
        return True
    return text[:1].isupper() and _looks_like_a_name(text)


def _looks_like_a_name(candidate: str) -> bool:
    """Whether a run of capitalised words is somebody's name rather than ordinary prose.

    Every word being an ordinary word ("Please Action", "Customer Support") means it is prose. One
    word the stoplist does not know ("Jane Okonkwo", "Acme Health") means it may be a name, and a
    may-be is redacted.
    """
    words = [word.lower() for word in candidate.split()]
    return any(word not in _NAME_STOPWORDS for word in words)


#: Tokens never treated as part of a person's or an organisation's name on their own.
_NAME_STOPWORDS = frozenset({
    "the", "and", "ltd", "inc", "llc", "plc", "gmbh", "limited", "company", "group", "team",
    "support", "customer", "account", "service", "services", "bank",
    "please", "action", "today", "note", "notes", "call", "called", "escalating", "escalated",
    "refund", "refunds", "dispute", "disputes", "charge", "duplicate", "details", "pasted",
    "flagged", "under", "runbook", "spelled", "with", "again", "rang", "wrote", "reply", "says",
    "assistants", "about", "this", "that", "from", "using", "rotate", "pulled", "primary",
    "agent", "agents", "lead", "role", "roles", "tool", "tools", "record", "records",
})


def name_tokens(name: str) -> tuple[str, ...]:
    """The parts of a name worth striking on their own.

    A note that says "Jane Okonkwo rang; Okonkwo spells it with a K" leaks the surname even after
    the full name is replaced, and surnames on their own are how people actually write notes. Tokens
    of four characters or more that are not ordinary words are substituted too; shorter ones are
    left, because a three-letter token is as likely to be a word as a name.
    """
    parts = [part.strip(".,;:()") for part in name.split()]
    return tuple(
        part
        for part in parts
        if len(part) >= 4 and part.lower() not in _NAME_STOPWORDS and part[:1].isupper()
    )


def _is_personal_field(path: str, key: str, hints: tuple[str, ...] = PERSON_FIELD_HINTS) -> bool:
    """Whether this path holds somebody's name or organisation, rather than the report's own."""
    if key.lower() not in hints:
        return False
    root = path.split(".", 1)[0].split("[", 1)[0]
    return root in PERSONAL_PATH_PREFIXES


@dataclass(frozen=True)
class Finding:
    """One thing the scrubber replaced, and where."""

    kind: str
    original: str
    replacement: str
    path: str

    def __str__(self) -> str:
        return f"{self.path}: {self.kind} -> {self.replacement}"


@dataclass
class Scrubber:
    """A deterministic redactor with a stable alias table.

    The table is the point: the same original always gets the same placeholder within one report,
    so ``REC-88213`` referenced by the request, the twin and a grading condition stays one record
    and the redacted report still validates. The table is per-report, never persisted — a
    cross-report table would itself be a re-identification key.
    """

    extra_names: tuple[str, ...] = ()
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    _path: str = ""

    def alias(self, kind: str, original: str) -> str:
        key = (kind, original)
        if key not in self.aliases:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            self.aliases[key] = self._format(kind, self.counters[kind])
        return self.aliases[key]

    @staticmethod
    def _format(kind: str, number: int) -> str:
        if kind == "EMAIL":
            return f"person{number}@example.invalid"
        if kind == "PHONE":
            return f"+1-555-01{number:02d}"
        if kind == "HOST":
            return f"host{number}.internal.example"
        if kind == "IP":
            return f"203.0.113.{number}"
        return f"{kind}_{number:04d}"

    def _record(self, kind: str, original: str, replacement: str) -> None:
        self.findings.append(Finding(kind, original, replacement, self._path or "<root>"))

    # -- the rules ----------------------------------------------------------------------

    def literal(self, kind: str, original: str) -> str:
        """Replace a whole value that we already know is sensitive from its field name."""
        if not original.strip() or _PLACEHOLDER.match(original.strip()):
            return original
        replacement = self.alias(kind, original.strip())
        self._record(kind, original, replacement)
        return replacement

    def text(self, value: str, names: tuple[str, ...], personal_prose: bool = False) -> str:
        """Every pattern rule, over one free-text value, in an order that matters.

        Keys first: a secret pasted into a note must not be partly eaten by the phone rule.
        Structured identifiers next, then hosts, then the harvested names, which are plain literals
        and would otherwise be cut in half by an earlier rule.
        """
        result = value
        for pattern, kind in _KEY_RULES:
            result = self._sub(pattern, kind, result)
        result = self._sub(_EMAIL, "EMAIL", result)
        result = self._sub(_SSN, "SSN", result)
        result = self._sub(_IBAN, "IBAN", result)
        result = self._sub(_CARD, "CARD", result)
        result = self._sub_group(_ACCOUNT_LABELLED, "ACCT", result)
        result = self._sub(_PHONE, "PHONE", result, guard=_looks_like_phone)
        result = self._sub(_INTERNAL_HOST, "HOST", result)
        result = self._sub(_PRIVATE_IP, "IP", result)
        result = self._sub_group(_URL_HOST, "HOST", result, only_internal=True)
        result = self._sub_group(_HONORIFIC, "PERSON", result)
        if personal_prose:
            result = self._sub_group(_LIKELY_PERSON, "PERSON", result, guard=_looks_like_a_name)
        for name in sorted(names, key=len, reverse=True):
            if not name or _PLACEHOLDER.match(name):
                continue
            replacement = self.alias("PERSON", name)
            for literal in (name, *name_tokens(name)):
                pattern = re.compile(rf"(?<![\w]){re.escape(literal)}(?![\w])", re.IGNORECASE)
                if pattern.search(result):
                    result = pattern.sub(replacement, result)
                    self._record("PERSON", literal, replacement)
        return result

    def _sub(
        self,
        pattern: Pattern[str],
        kind: str,
        value: str,
        guard: Optional[Callable[[str], bool]] = None,
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            original = match.group(0)
            if _PLACEHOLDER.match(original) or (guard is not None and not guard(original)):
                return original
            replacement = self.alias(kind, original)
            self._record(kind, original, replacement)
            return replacement

        return pattern.sub(replace, value)

    def _sub_group(
        self,
        pattern: Pattern[str],
        kind: str,
        value: str,
        only_internal: bool = False,
        guard: Optional[Callable[[str], bool]] = None,
    ) -> str:
        """Replace only the capturing group, keeping the label that made it recognisable.

        ``ACCT-99812`` becomes ``ACCT-ACCT_0001`` rather than a bare placeholder, so a reader can
        still see that the field held an account number — which is part of what makes the report
        reproducible — without the number itself.
        """

        def replace(match: re.Match[str]) -> str:
            original = match.group(1)
            if _PLACEHOLDER.match(original):
                return match.group(0)
            if kind == "ACCT" and not _HAS_DIGIT.search(original):
                # A label followed by letters only is a word, not an account number.
                return match.group(0)
            if guard is not None and not guard(original):
                return match.group(0)
            if only_internal and not (
                _INTERNAL_HOST.search(original) or _PRIVATE_IP.search(original)
            ):
                return match.group(0)
            replacement = self.alias(kind, original)
            self._record(kind, original, replacement)
            return match.group(0).replace(original, replacement)

        return pattern.sub(replace, value)

    # -- walking a report ---------------------------------------------------------------

    def harvest_names(self, payload: Any) -> tuple[str, ...]:
        """Names and organisations read out of fields whose key says what they hold.

        Harvesting before substituting is what lets the scrubber strike a person out of a sentence:
        a name in a free-text note is unrecognisable on its own, but trivial once the world has
        told us that ``customer_name`` is ``Jane Okonkwo``.
        """
        found: list[str] = list(self.extra_names)

        def visit(item: Any, path: str) -> None:
            if isinstance(item, dict):
                for key, value in item.items():
                    child = f"{path}.{key}" if path else str(key)
                    lowered = str(key).lower()
                    if (
                        isinstance(value, str)
                        and _is_personal_field(child, lowered)
                        and _is_a_person_value(lowered, value)
                    ):
                        text = value.strip()
                        if text and not _PLACEHOLDER.match(text):
                            found.append(text)
                    visit(value, child)
            elif isinstance(item, (list, tuple)):
                for index, value in enumerate(item):
                    visit(value, f"{path}[{index}]")

        visit(payload, "")
        return tuple(dict.fromkeys(found))

    def walk(self, payload: Any, names: tuple[str, ...], path: str = "") -> Any:
        if isinstance(payload, dict):
            out: dict[str, Any] = {}
            for key, value in payload.items():
                child = f"{path}.{key}" if path else str(key)
                lowered = str(key).lower()
                if lowered in NEVER_SCRUB_KEYS:
                    out[key] = value
                elif isinstance(value, str) and _is_personal_field(child, lowered, ORG_FIELD_HINTS):
                    self._path = child
                    out[key] = self.literal("ORG", value)
                elif (
                    isinstance(value, str)
                    and _is_personal_field(child, lowered)
                    and _is_a_person_value(lowered, value)
                ):
                    self._path = child
                    out[key] = self.literal("PERSON", value)
                else:
                    out[key] = self.walk(value, names, child)
            return out
        if isinstance(payload, list):
            return [self.walk(item, names, f"{path}[{index}]") for index, item in enumerate(payload)]
        if isinstance(payload, str):
            self._path = path
            root = path.split(".", 1)[0].split("[", 1)[0]
            return self.text(payload, names, personal_prose=root in PERSONAL_PATH_PREFIXES)
        return payload


@dataclass(frozen=True)
class ScrubResult:
    """The redacted report, what was replaced, and the payloads the diff is rendered from."""

    report: MisbehaviourReport
    findings: tuple[Finding, ...]
    original_payload: dict[str, Any]
    redacted_payload: dict[str, Any]

    @property
    def changed(self) -> bool:
        return bool(self.findings)

    @property
    def originals(self) -> tuple[str, ...]:
        """Every literal that was taken out. The scrubber's own test asserts that none of these
        survives anywhere in the redacted output, which is the only assertion that really matters."""
        return tuple(dict.fromkeys(finding.original for finding in self.findings))


def scrub(report: MisbehaviourReport, extra_names: tuple[str, ...] = ()) -> ScrubResult:
    """De-identify a report locally.

    The result is re-validated as a :class:`MisbehaviourReport`, so a redaction that would have
    broken a cross-reference (a record id replaced in one place and not another) fails here rather
    than in a reviewer's lap.
    """
    original = report.model_dump(mode="json")
    scrubber = Scrubber(extra_names=extra_names)
    names = scrubber.harvest_names(original)
    redacted_payload = scrubber.walk(original, names)
    redacted = MisbehaviourReport.model_validate(redacted_payload)
    return ScrubResult(
        report=redacted,
        findings=tuple(scrubber.findings),
        original_payload=original,
        redacted_payload=redacted.model_dump(mode="json"),
    )
