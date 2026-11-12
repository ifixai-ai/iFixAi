# Filing a misbehaviour report

Anyone may file. You do not need to work at iFixAi, use its runtime, or have an account on anything.
A report is a YAML file that passes `ifixai report validate`, and the usual way to send one is a pull
request this tool opens for you.

Read [register-spec.md](register-spec.md) first for what each field is and why it is required. This
page is the mechanics.

## The short version

```bash
# 1. You ran something and it misbehaved. Is it already known?
ifixai report signature --family authority_boundary --failure authority.violation --trigger injected_note

# 2. Draft it (or write the YAML by hand — nothing requires this tool).
#    Fill in every "TODO:" the draft leaves you.

# 3. See exactly what would be published, with nothing sent.
ifixai report review my-report.yaml

# 4. File it. This shows you the side-by-side again and asks you to approve.
ifixai report submit my-report.yaml --as "your name" --checkout /path/to/your/iFixAi/clone
```

Step 4 de-identifies locally, prints the original beside the redaction, asks you to confirm the
de-identified and rights-cleared flags, asks you to type `publish`, and only then commits and opens
the pull request. Stopping at any prompt sends nothing.

## What happens to your data

In this order, always, with no flag that changes it:

1. **De-identification runs locally, before anything else.** Names, emails, phone numbers, account
   and record ids, internal hostnames, private IPs, API keys and secrets, and names buried in
   free-text notes are replaced with stable placeholders. Stable matters: a record referred to in
   three places stays one record, so the report still validates.
2. **You see the original beside the redaction, line for line.** Not a summary and not a count — the
   actual text, because what a scrubber misses is by definition what a summary would not mention.
   Add `--name "Real Name"` (repeatable) for anything you know the scrubber will not recognise.
3. **You confirm both provenance flags by name.** De-identified, and rights-cleared. The second one
   no tool can check for you.
4. **You type the confirmation word.** Then, and not before, a branch is pushed and a PR opened.

The approval is bound to the exact bytes you approved. Edit one character afterwards and the
submission is refused until you review and approve the new version.

**The scrubber is a help, not a guarantee.** It is deterministic and tested against realistic nasties
(see `ifixai/tests/register/test_scrubber_nasties.py`), and it still cannot know that `Project
Bluebird` is a client codename. You are the last check, which is why you are shown everything.

If your report is audit-derived, it will be refused unless both flags are set. That is deliberate:
one leaked customer record would end this register.

## If you are an AI agent reading this

You may prepare a submission. You may not make one.

The MCP server (`ifixai report mcp`) gives you `register_describe_spec`,
`register_compute_signature`, `register_validate_report` and `register_prepare_submission`. There is
no submit tool, and the server has no code path that can reach the network. Prepare the draft, save
it, and tell the person at the keyboard to run `ifixai report submit <path>`. Do not attempt to open
the pull request yourself with `git` or `gh`: the person has to see what would be published and
approve it, and that is not a step you can take for them.

To wire the server up, add to your editor's MCP configuration:

```json
{ "mcpServers": { "ifixai-register": { "command": "ifixai", "args": ["report", "mcp"] } } }
```

## What CI checks on your pull request

A submission PR touching `register/candidates/**` or `register/entries/**` is checked by
[`.github/workflows/register-submission.yml`](../.github/workflows/register-submission.yml), which
runs the same validator you can run locally. It checks:

- **Schema conformance** — the file parses as a misbehaviour report, spec version 1.
- **The rules** — the twin is a real twin, the grading checks name conditions that exist and read the
  right source, the signature digest matches its triple, counts are counts.
- **Calibration present** — at least one reproduction that currently calibrates, with the reference
  solution at 1 and the null action at 0.
- **De-identified and rights-cleared both set** for an `audit_derived` report.
- **No leftover `TODO:` placeholders** — an unfinished draft is not a submission.
- **Duplicate signatures** — flagged against every existing entry and candidate, with the id of the
  one you may want to add to instead.
- **Candidates claim no id** — `IFX-YYYY-NNNN` ids come from curation.

A submission that fails is **not merged**, and the check writes a readable reason into the job
summary saying which field and what to change. Fix it and push; the check runs again. A duplicate
flag is not a failure — a curator decides whether your finding joins an existing entry or becomes a
new one.

## Review, and what `accepted` means

Your file lands in `register/candidates/` as a candidate. It carries no `IFX` id and claims none.
Curation reads it and either accepts it — at which point it moves to `register/entries/` with a
permanent `IFX-YYYY-NNNN` id — or leaves it as a candidate, or rejects it with a recorded reason.

An id, once issued, is never removed or reused. A mistaken entry keeps its id and is marked
`rejected`, as a rejected CVE record is, so a citation never silently changes meaning.

## Filing without this tool

Nothing requires the CLI. Write the YAML against
[the schema](../ifixai/schemas/misbehaviour-report-1.schema.json), put it in
`register/candidates/IFXC-<digest12>-<YYYYMMDD>.yaml`, and open a pull request. CI checks the file,
not how you made it. If you do this, you are taking on the de-identification yourself — read the
provenance section of the spec before you do.

## If you cannot publish it

Some findings should not be in a public register — an unpatched flaw in a named vendor's model under
embargo, or anything you are not entitled to publish. Do not file those here. See
[SECURITY.md](../SECURITY.md) for private disclosure.
