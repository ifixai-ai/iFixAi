# Filing a misbehaviour report

> Make "is this agent safe to deploy" a question with an answer, not an opinion.

AI agents are being put into real systems faster than anyone can verify they respect the boundaries
around them. When one fails — uses a tool it shouldn't, acts without authorisation, follows an
instruction planted in a record — that failure is found privately, fixed quietly, and rediscovered
six months later by a different team who had no way of knowing.

This register exists to end that. Every entry is a reproducible measurement rather than an anecdote:
a permanent id, exact model and harness versions, how often it happened out of how many attempts,
and a task anyone can run to see it for themselves.

That gives three groups something they don't have today. Developers can test for known failures
before they ship, the way CVEs let them test for known vulnerabilities. Model developers get signal
from real deployments that no internal evaluation can produce, because production surfaces failures
a lab never thinks to look for. Buyers and regulators get something they can cite, instead of taking
a vendor's word for it.

## Why you would bother

Two things come back to you, and they are the honest ones rather than the flattering ones:

- **Early sight of entries affecting models you use.** The register is a comparison table, and the
  negative results in it — models tested that did *not* show a behaviour — are as useful when you
  are choosing a model as the positive ones.
- **A permanent, citable reference for your own finding.** An accepted entry gets an id that never
  changes and never gets reused, so you can point at it in your own security reviews, audits and
  vendor conversations instead of re-explaining the problem each time.

That is the whole list. The register does not yet do anything else for contributors, and this
section will grow only when that stops being true.

## Filing one

Anyone may file. You do not need to work at iFixAi, use its runtime, or have an account on anything.
A report is a YAML file that passes `ifixai report validate`, and the usual way to send one is a pull
request this tool opens for you.

Read [register-spec.md](register-spec.md) first for what each field is and why it is required. This
page is the mechanics.

Reports go to [ifixai-ai/agent-misbehavior-registry](https://github.com/ifixai-ai/agent-misbehavior-registry),
as pull requests.

## The short version

```bash
# Once per organisation: have somebody who can authorise it fill this in and commit it.
ifixai report clearance init

# You ran something and it misbehaved. Is it already known?
ifixai report signature --family authority_boundary --failure authority.violation --trigger injected_note

# What a run would show you, de-identified, with nothing asked and nothing sent.
ifixai report offer my-report.yaml --show

# File it: the same preparation, then one question.
ifixai report submit my-report.yaml --as "your name" --checkout /path/to/registry/clone
```

`submit` de-identifies locally **first**, prints the signature, the headline numbers and a summary of
exactly which fields were redacted and how many times, and then asks once:

```
Agent failures like this aren't published anywhere today.
Contributing makes it reproducible, so every team can test for it.
Contribute to the public catalogue? [y/N]
```

`N` is the default and a perfectly ordinary answer. `--show` prints the whole original-versus-redacted
diff first, for anyone who wants to read it all before deciding. Nothing touches the network before
the `y`.

With a valid standing clearance (below) that is the only question. Without one, you are also asked
whether you are entitled to publish this, because no tool can check that for you.

## What happens to your data

In this order, always, with no flag that changes it:

1. **De-identification runs locally, before anything else.** Names, emails, phone numbers, account
   and record ids, internal hostnames, private IPs, API keys and secrets, and names buried in
   free-text notes are replaced with stable placeholders. Stable matters: a record referred to in
   three places stays one record, so the report still validates.
2. **You see what was redacted, with the numbers you are publishing.** A count per category, above
   the question, so the thing you are agreeing to is on the screen when you answer. `--show` gives
   you the whole diff line for line. Add `--name "Real Name"` (repeatable) for anything you know the
   scrubber will not recognise — a client codename, a project name.
3. **You answer once.** `[y/N]`, defaulting to No.
4. **Publication rights**: answered in advance by your organisation's standing clearance, or asked
   here if there is none. Then, and not before, a branch is pushed and a PR opened.

The approval is bound to the exact bytes you approved. Edit one character afterwards and the
submission is refused until you review and approve the new version.

## Standing clearance, so filing is one keystroke

De-identification is something a tool can do. **Publication rights are not**: only your organisation
can say whether its agreements permit publishing a finding derived from one of its systems. Asking
you that per submission is correct and also means nobody files anything, because nobody stops
mid-debugging to read a contract.

So it is answered once, by somebody who can actually answer it, in a file you commit:

```yaml
# .ifixai/contribution.yaml
version: "1"
organisation: Acme Health Partners
authorised_by: R. Mbeki, Head of Platform Security
scope: Synthetic and de-identified agent misbehaviour findings from our own staging systems.
expires_on: 2027-03-31
excludes:
  - anything derived from production customer records
```

`ifixai report clearance init` writes the template; `ifixai report clearance show` says whether what
is there is still valid. It is committed on purpose: that makes it reviewable, diffable and
revocable by the same process as any other policy in your repository. It expires on purpose too, so
a clearance granted by someone who has since left does not authorise submissions forever.

A submission records which clearance it relied on — who authorised it, for what scope, until when —
so there is an auditable trail rather than a checkbox. The `excludes` list stays in your repository;
it is your policy, not the register's business.

**What a clearance does not do is remove you from the loop.** It answers the rights question in
advance. The one question that matters is still asked, every time, and nothing in that file can
answer it.

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

A submission PR touching `examples/register/candidates/**` or `examples/register/entries/**` is checked by
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
- **Candidates claim no id** — `AGENT-YYYY-NNNN` ids come from curation.

A submission that fails is **not merged**, and the check writes a readable reason into the job
summary saying which field and what to change. Fix it and push; the check runs again. A duplicate
flag is not a failure — a curator decides whether your finding joins an existing entry or becomes a
new one.

## Review, and what `accepted` means

Your file lands in `examples/register/candidates/` as a candidate. It carries no `AGENT` id and claims none.
Curation reads it and either accepts it — at which point it moves to `examples/register/entries/` with a
permanent `AGENT-YYYY-NNNN` id — or leaves it as a candidate, or rejects it with a recorded reason.

An id, once issued, is never removed or reused. A mistaken entry keeps its id and is marked
`rejected`, as a rejected CVE record is, so a citation never silently changes meaning.

## Filing without this tool

Nothing requires the CLI. Write the YAML against
[the schema](../ifixai/schemas/misbehaviour-report-1.schema.json), put it in
`examples/register/candidates/CAND-<digest12>-<YYYYMMDD>.yaml`, and open a pull request. CI checks the file,
not how you made it. If you do this, you are taking on the de-identification yourself — read the
provenance section of the spec before you do.

## If you cannot publish it

Some findings should not be in a public register — an unpatched flaw in a named vendor's model under
embargo, or anything you are not entitled to publish. Do not file those here. See
[SECURITY.md](../SECURITY.md) for private disclosure.
