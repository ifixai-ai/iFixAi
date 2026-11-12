"""The scrubber, against the things that actually turn up in an audit fixture.

The assertion that matters is the last one in most of these tests: **the original literal does not
appear anywhere in the redacted output.** Not "a placeholder was produced" — the real value is gone,
from the structured fields and from the free text, everywhere in the document.

One leaked customer record would end this register, so this file is written to be pessimistic: every
case is a value somebody would plausibly have in a real fixture, and the realistic-nasties test at
the end puts all of them into one report at once, including a person's name buried mid-sentence in a
note, which is the case a field-by-field redactor misses.
"""

from __future__ import annotations

import json

import pytest

from ifixai.register import scrub
from ifixai.register.spec import MisbehaviourReport

pytestmark = pytest.mark.unit


# Secret-shaped fixtures are assembled from fragments rather than written as literals. They are
# invented, but a literal that looks like a live token trips GitHub's push protection on the way in
# and every secret scanner downstream of it, and a test file is a bad place to argue about that. The
# value the scrubber sees is identical either way.
def _fake(*parts: str) -> str:
    return "".join(parts)


FAKE_SECRETS = {
    "anthropic": _fake("sk-", "ant-", "api03-", "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"),
    "openai": _fake("sk-", "proj-", "9x8W7v6U5t4S3r2Q1p0OnMlKjIhGfEdCbA"),
    "aws": _fake("AKIA", "IOSFODNN7", "EXAMPLE"),
    "github": _fake("ghp", "_", "AbCdEfGhIjKlMnOpQrStUvWxYz0123456"),
    "slack": _fake("xox", "b-", "2374829348-2938472938-", "AbCdEfGhIjKlMnOp"),
    "google": _fake("AIza", "Sy", "B1cD3fG4hI5jK6lM7nO8pQ9rS0tU1vW2xY"),
    "huggingface": _fake("hf", "_", "AbCdEfGhIjKlMnOpQrStUvWxYz01234567"),
}
FAKE_JWT = _fake(
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkphbmUgT2tvbmt3byJ9",
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
)
FAKE_CARD = _fake("4111 ", "1111 ", "1111 ", "1111")


def _redact(report: MisbehaviourReport, *names: str) -> scrub.ScrubResult:
    return scrub.scrub(report, tuple(names))


def _text(result: scrub.ScrubResult) -> str:
    return json.dumps(result.redacted_payload, ensure_ascii=False)


def _with_note(entry: MisbehaviourReport, note: str) -> MisbehaviourReport:
    records = list(entry.world.records)
    records[0] = records[0].model_copy(update={"fields": {**records[0].fields, "note": note}})
    world = entry.world.model_copy(update={"records": tuple(records)})
    return entry.model_copy(update={"world": world})


@pytest.mark.parametrize(
    "nasty",
    [
        "jane.okonkwo@acme-health.co.uk",
        "payroll+alerts@internal.acme.corp",
        "+44 20 7946 0958",
        "(415) 555-0132",
        "415-555-0132",
        "+1 415 555 0132",
        "07700 900461",
        "123-45-6789",
        FAKE_CARD,
        "GB29NWBK60161331926819",
        "ACCT-99812736",
        "customer CUST_4451209",
        "policy POL-8812-XY",
        "db-primary-07.internal",
        "jenkins.acme.corp",
        "wiki.acme.intranet",
        "10.42.7.19",
        "192.168.1.44",
        FAKE_SECRETS["anthropic"],
        FAKE_SECRETS["openai"],
        FAKE_SECRETS["aws"],
        FAKE_SECRETS["github"],
        FAKE_SECRETS["slack"],
        FAKE_SECRETS["google"],
        FAKE_SECRETS["huggingface"],
    ],
)
def test_a_nasty_in_a_free_text_note_does_not_survive(entry: MisbehaviourReport, nasty: str) -> None:
    report = _with_note(entry, f"Customer called back about this. Details: {nasty}. Please action today.")
    result = _redact(report)
    assert result.changed, f"the scrubber did not recognise {nasty!r}"
    assert nasty not in _text(result)


def test_a_jwt_in_a_note_does_not_survive(entry: MisbehaviourReport) -> None:
    token = FAKE_JWT
    result = _redact(_with_note(entry, f"Support attached their session token: {token}"))
    assert token not in _text(result)


def test_a_private_key_header_does_not_survive(entry: MisbehaviourReport) -> None:
    result = _redact(_with_note(entry, "Pasted by the customer:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB..."))
    assert "BEGIN RSA PRIVATE KEY" not in _text(result)


def test_a_name_from_a_structured_field_is_struck_from_free_text_too(entry: MisbehaviourReport) -> None:
    """The case a field-by-field redactor misses: the name is a value of `customer`, and also sits
    mid-sentence in a note where no pattern would find it."""
    records = list(entry.world.records)
    records[0] = records[0].model_copy(
        update={
            "fields": {
                **records[0].fields,
                "customer": "Jane Okonkwo",
                "note": "Jane Okonkwo rang again; she says Okonkwo is spelled with a K. Escalating.",
            }
        }
    )
    world = entry.world.model_copy(update={"records": tuple(records)})
    result = _redact(entry.model_copy(update={"world": world}))
    text = _text(result)
    assert "Jane Okonkwo" not in text
    assert "Okonkwo" not in text


def test_an_extra_name_the_scrubber_could_not_know(entry: MisbehaviourReport) -> None:
    """A client codename is not a pattern. `--name` is how the submitter adds it, and the point of
    showing them the diff is that they can see they need to."""
    report = _with_note(entry, "Flagged under Bluebird; see the Bluebird runbook.")
    assert "Bluebird" in _text(_redact(report)), "a one-word codename is not a pattern"
    result = _redact(report, "Bluebird")
    assert "Bluebird" not in _text(result)


def test_a_name_only_in_free_text_is_still_caught(entry: MisbehaviourReport) -> None:
    """The hardest case: a person named in a note and nowhere structured, so nothing told the
    scrubber it was a name. Two adjacent capitalised words inside somebody's data are redacted."""
    result = _redact(_with_note(entry, "Call Jane Okonkwo on Tuesday about the chargeback."))
    assert "Jane Okonkwo" not in _text(result)


def test_the_report_own_vocabulary_is_not_mistaken_for_a_name(entry: MisbehaviourReport) -> None:
    """The heuristic runs over the world and the identities, not over the report's prose, where
    "Issue Refund" and "Authority Boundary" are the vocabulary."""
    result = _redact(entry)
    assert result.report.tools[2].name == "Issue refund"
    assert result.report.title == entry.title
    assert result.report.summary == entry.summary


def test_honorific_names_are_caught_without_a_roster(entry: MisbehaviourReport) -> None:
    result = _redact(_with_note(entry, "Dr. Amara Nwosu asked for a callback."))
    assert "Amara Nwosu" not in _text(result)


def test_everything_at_once(entry: MisbehaviourReport) -> None:
    """One realistic record with every category in it, asserted as a set: nothing survives."""
    nasties = {
        "name": "Jane Okonkwo",
        "email": "jane.okonkwo@acme-health.co.uk",
        "phone": "+44 20 7946 0958",
        "account": "ACCT-99812736",
        "record": "CUST_4451209",
        "host": "db-primary-07.internal",
        "ip": "10.42.7.19",
        "key": FAKE_SECRETS["anthropic"],
        "company": "Acme Health Partners",
    }
    records = list(entry.world.records)
    records[0] = records[0].model_copy(
        update={
            "fields": {
                **records[0].fields,
                "customer_name": nasties["name"],
                "contact": nasties["email"],
                "company": nasties["company"],
                "note": (
                    f"{nasties['name']} ({nasties['email']}, {nasties['phone']}) disputes a charge on "
                    f"{nasties['account']} / {nasties['record']}. Ops pulled the row from "
                    f"{nasties['host']} ({nasties['ip']}) using {nasties['key']} — rotate that."
                ),
            }
        }
    )
    world = entry.world.model_copy(update={"records": tuple(records)})
    result = _redact(entry.model_copy(update={"world": world}))
    text = _text(result)
    survivors = [label for label, value in nasties.items() if value in text]
    assert survivors == [], f"these survived redaction: {survivors}"


def test_redaction_is_stable_so_cross_references_still_resolve(entry: MisbehaviourReport) -> None:
    """A record named in the request, the twin and a grading condition must stay one record, or the
    redacted report would no longer validate."""
    result = _redact(entry)
    # The redacted report is re-validated inside scrub(); reaching here means it did.
    assert result.report.twin.record_id in result.report.world.record_ids
    assert result.report.twin.request_text == result.report.request.text


def test_the_same_original_always_gets_the_same_placeholder(entry: MisbehaviourReport) -> None:
    report = _with_note(entry, "jane@acme.test wrote in. Reply to jane@acme.test.")
    result = _redact(report)
    note = result.report.world.records[0].fields["note"]
    assert "jane@acme.test" not in note
    first, second = note.split("wrote in. Reply to ")
    assert first.strip() == second.rstrip(".").strip()


def test_already_redacted_values_are_left_alone(entry: MisbehaviourReport) -> None:
    """The worked example ships already de-identified; scrubbing it again must not churn it."""
    once = _redact(entry)
    twice = scrub.scrub(once.report)
    assert twice.redacted_payload == once.redacted_payload


def test_the_shipped_examples_need_no_redaction(
    entry: MisbehaviourReport, candidate: MisbehaviourReport
) -> None:
    """The worked examples are already de-identified, so the scrubber must find nothing in them. A
    false positive here would mean the redactor mangles the register's own files — it used to turn
    `AGENT-2026-0001` into a phone number and a queue named "supervisor review" into a person."""
    for report in (entry, candidate):
        result = _redact(report)
        assert result.findings == (), [str(finding) for finding in result.findings]


def test_findings_name_where_each_replacement_happened(entry: MisbehaviourReport) -> None:
    result = _redact(_with_note(entry, "Call +44 20 7946 0958."))
    assert any("world.records" in finding.path for finding in result.findings)
    assert all(finding.kind and finding.replacement for finding in result.findings)
