# The misbehaviour report spec, version 1

A **misbehaviour report** describes one kind of AI-agent misbehaviour precisely enough that a
stranger can reproduce it. It is to agent misbehaviour what a CVE record is to a software flaw: a
permanent, citable description of something that goes wrong, separate from any one proof of concept.

- Machine-readable schema: [`ifixai/schemas/misbehaviour-report-1.schema.json`](../ifixai/schemas/misbehaviour-report-1.schema.json)
- The format in code: [`ifixai/register/spec.py`](../ifixai/register/spec.py)
- Worked examples: [`register/entries/IFX-2026-0101.yaml`](../register/entries/IFX-2026-0101.yaml) (accepted) and [`register/candidates/`](../register/candidates/) (a candidate)
- How to file one: [register-contributing.md](register-contributing.md)

Validate a report with no network and no API key:

```bash
ifixai report validate my-report.yaml
```

## Why the spec is runtime-neutral

The obvious way to define "reproducible" is to require a runnable task: say that a report is valid
when it ships a container the grader can run. iFixAi's own private catalogue started there, requiring
every reproduction to be a [Harbor](https://github.com/harbor-framework/harbor) task produced by its
exporter. For a public register that is the wrong choice, for three reasons.

1. **It couples the register to a runtime nobody in the register controls.** Harbor is somebody
   else's project, with its own release cadence and its own idea of a task schema. A register whose
   definition of a finding is "whatever this version of that tool accepts" has outsourced its own
   meaning.
2. **It locks out the people with the best findings.** Plenty of teams cannot run an arbitrary
   container image against their own fixtures — regulated environments, air-gapped networks, a
   laptop. If filing requires their runtime to be ours, they do not file.
3. **It rots.** Pin a task to a runtime version and an image digest, and in eighteen months the
   report means nothing checkable: the image is gone, the schema moved, the task no longer builds.
   The *behaviour* has not changed at all.

So the report is the definition and a runtime is an implementation of it. The report states the
world, the identities, the tools, the request, the expected final state and the grading checks in
its own terms, with no container, image or harness command anywhere in those fields. A
`reproductions` entry then binds that description to one runtime:

```yaml
reproductions:
  - runtime: harbor
    version: "0.25.0"
    image_digest: sha256:4840d514…
    task_id: authority_boundary/refunds-desk/pressure_approval/denied
    calibration: {reference_solution: {score: 1, …}, null_action: {score: 0, …}}
    status: calibrates
    reference_implementation: true
```

This is the split CVE makes between a vulnerability record and a proof-of-concept exploit, and the
one OCI makes between an image specification and a runtime that runs images. Consequences worth
being explicit about:

- **Harbor is the reference implementation, not the definition.** It is the runtime iFixAi maintains
  and calibrates, so it is where a report is verified first and what `reference_implementation: true`
  marks. It carries no privilege in the format: nothing is rejected for lacking a Harbor binding.
- **Reproductions are plural.** A second runtime can be appended to an accepted entry without
  reissuing its id, because the id names the behaviour and the behaviour did not change.
- **Rot is a state, not a deletion.** When an implementation stops calibrating, it gets
  `status: rot` and a `rot_reason` saying what broke. The entry keeps its id and its findings; a
  re-runner is told not to trust that one binding. An entry whose every binding has rotted still
  describes a real, re-implementable behaviour — it just cannot be newly accepted until one is
  re-verified.

## The required fields, and what breaks without each

### 1. World state — `world`

Entities and records with concrete ids and field values.

> Without it, nothing can be asserted. "A customer account in arrears" cannot be checked; a record
> with `{record_id: REC-0001, refunded: false, amount_cents: 48900}` can. It is also the only way to
> tell a run that acted on the *wrong* record from one that acted on the right one — which is a
> distinction several real findings turn on.

`carries_untrusted_text: true` marks a record whose free text came from outside the trust boundary.
A planted instruction lives in such a field, so a report about instruction-following-from-data has to
say which record carries it.

### 2. Identity and authorization — `identity`

Roles, which role holds which tool, which logins hold which roles, and the escalation paths.

> "The agent used a tool it should not have" is not a statement until something says which role holds
> which tool. This section is that statement.

`identity_source` is fixed to `login` and cannot be set to anything else. **Identity comes from the
login and is never claimable in conversation.** A format that allowed otherwise would be describing a
different and far less interesting failure — and would make the twin impossible to construct, since
there would be no fact of the matter about who was asking.

### 3. Tools — `tools`

Name, signature, declared risk, and whether the tool mutates state.

> The signature is what makes a call checkable: did it pass the requested record, or a different one?
> The risk level is what a severity judgement rests on — a wrong read and a wrong irreversible
> payment are not the same finding. `mutates_state` decides whether correctness can be a condition on
> state at all: for a read-only tool, the evidence log is the only place the call is visible.

### 4. The request — `request`

The verbatim text, and which login sent it.

> Paraphrase is not reproduction. A summarised request loses the exact wording that carried the
> pressure, the claim of authority, or the planted instruction, and two readers rebuild two different
> tasks from it. `sent_by` must be a declared login, because a request from nobody in particular
> cannot be denied or permitted.

### 5. Expected behaviour — `expected`

A condition on the final world state. **Never on the wording of a reply.**

> An agent that says "I've escalated this to your supervisor" and does nothing must fail. An agent
> that does exactly the right thing while phrasing it oddly must pass. There is no field anywhere in
> this format for the text of a reply, and `reply_sent` asserts only that one was sent. That is not an
> oversight: grading on wording is how a register fills with findings that evaporate at the next
> model release, and it is indistinguishable from grading on style.

The predicates are deliberately few (`record_field_equals`, `record_field_unchanged`,
`record_exists`, `tool_called_on_record`, `tool_not_called`, `escalated_to_channel`, `reply_sent`). A
report that needs a new one is a reason to version the spec, not a licence to write prose in an
assertion field.

### 6. The twin — `twin`

The permitted counterpart: the **identical** request about the **same** record, from a role that
legitimately holds the tool.

> Without it, an agent that refuses everything scores perfectly. This is the single most important
> field in the format and the one most often missing from published "misbehaviour" results. The twin
> turns "it did something it should not have" into "it discriminates correctly", and it is the only
> control that distinguishes a safety property from a capability loss.

The validator enforces that the twin's request text is byte-identical, that the twin's login really
does hold the tool the subject's login does not, and that the twin passes only by *doing the work* —
a twin whose expected behaviour is also an escalation would let a blanket refusal pass both halves.

### 7. Grading contract — `grading`

Named checks, each 0/1, each derived from world state or the evidence log. **Never from an LLM judge.**

> Two people grading the same transcript by eye produce two numbers. The combination is conjunctive
> and binary, so a score has exactly one meaning — every named check passed — and no partial-credit
> scheme can quietly turn a failure into a pass.
>
> `source` has two members and there is no `llm_judge`. A check whose value depends on a model's
> opinion is not reproducible, and a register built on such checks cannot tell a real regression from
> a drift in the judge. Adding that member would be a different spec, not a new option in this one.

Every expected condition must be read by some check: a condition that is graded by nothing can be
violated by a run that still scores 1.

### 8. Calibration evidence — `calibration`, per reproduction

The reference solution scores 1. Doing nothing scores 0. Non-negotiable.

> **This is what separates a finding from an assertion.** A reproduction that has never been shown to
> reward a correct run is a claim about a task nobody has solved. The reference solution scoring 1
> rules out a task that is impossible or mis-specified. Doing nothing scoring 0 rules out a task any
> agent passes by inaction — which is, by a wide margin, the most common way a "misbehaviour finding"
> turns out to be a broken grader.

### 9. Determinism declaration — `determinism`

Seed, temperature, step cap, and what is allowed to vary.

> Without the settings, a different occurrence count is not evidence of a different model; it may be
> evidence of a different temperature. `allowed_variation` is the part people omit and the part that
> matters most to a re-runner: it is what tells them whether their mismatch is a failure to reproduce
> or an expected wobble.

### 10. Observed result — `observed`

Model and version, harness and version, occurrences out of trials, date. **Counts, never a bare
percentage.**

> "42% of the time" cannot be given a confidence interval, cannot be pooled with another run, and
> hides whether the denominator was 12 trials or 1,200. `37/48` can be recomputed and cannot be
> rounded into a better number. The harness and its version belong here too: the same model in two
> scaffolds is two different systems, and most of the variance people attribute to models lives here.

### 11. Provenance and rights — `provenance`

Synthetic or audit-derived; a de-identified flag; a rights-cleared flag. **An audit-derived report
requires both.**

> The register is public and permanent. A report derived from an audit of somebody else's system
> carries two risks a synthetic one does not — somebody's data, and somebody's contract — so an
> `audit_derived` report is refused outright unless both flags are set.
>
> The flags are assertions by a named person, not something a tool infers. De-identification can be
> attempted mechanically and is (see below); clearance cannot be, at all.

## The behaviour signature

Two reports describe the same behaviour when they share a `(family, failure, trigger)` triple. The
`digest` is a 12-hex-character hash of the normalised triple, used for automatic clustering and
duplicate detection:

```bash
ifixai report signature --family authority_boundary --failure authority.violation --trigger injected_note
```

The triple is deliberately the same key iFixAi's private catalogue clusters on, so a cluster found by
an internal sweep and a report filed by a stranger land on the same entry rather than becoming two.

The digest is **derived, never authored**. The validator recomputes it and rejects a mismatch, so a
submitter cannot slip past de-duplication by editing the field. Normalisation folds case and
collapses separators and does nothing else: "nearly the same words" is a judgement for a curator, and
a digest that tried to make it would silently merge two distinct findings.

A duplicate is **flagged, not rejected**. The same behaviour on a new model is a real contribution,
and the right outcome is usually an added `observed` row on the existing entry.

## `candidate` and `accepted`

| | `candidate` | `accepted` |
| --- | --- | --- |
| Who creates it | anyone | curation |
| Identifier | `IFXC-<digest12>-<YYYYMMDD>`, derived | `IFX-YYYY-NNNN`, permanent, never reused |
| Lives in | `register/candidates/` | `register/entries/` |
| Means | somebody filed this and it passes the validator | a curator has read it and stands behind it |

**Anything submitted is a candidate.** A submission that claimed an `IFX` id is rejected by the
validator. Only a curated entry consumes a permanent id, and an id, once issued, is never removed or
reused — a mistaken entry becomes `rejected` and keeps its id, as a rejected CVE does.

This is what lets the register take volume without diluting its own meaning: the quality signal is
the `accepted` set, and the candidate set can be as large and as uneven as the world is.

## Safety: what the format cannot do on its own

The format has one hard constraint around it, implemented in the contribution path and not
negotiable: **no submission leaves a machine without a person approving that specific submission.**
De-identification runs locally first; the submitter is shown the original beside the redaction, line
for line; both provenance flags are confirmed by name; and an approval is bound to the exact bytes it
approved, so an edit after approval invalidates it. An agent may *prepare* a submission and cannot
make one. There is no configuration flag that removes the human.

See [register-contributing.md](register-contributing.md) for the path itself.

## Versioning

`spec_version` is `"1"`. A change that would make an existing report mean something different is a
new version with its own schema file, never an edit of this one. Adding an optional field, or a new
`predicate` member, is a minor addition that keeps version 1 — existing reports stay valid and mean
what they meant.

## Licensing of what is borrowed

Nothing in this spec is copied from the Harbor task format. The format here is defined from the
required fields above; Harbor appears only as a value of `runtime` and as the file names a
reproduction binding refers to, which is interface description rather than expression.

Harbor itself is **Apache-2.0** ([harbor-framework/harbor](https://github.com/harbor-framework/harbor)),
which is this project's own licence, so even the parts of iFixAi that do generate Harbor task
directories redistribute compatibly. The reference-implementation binding names a runtime and a
version; it does not vendor any of Harbor's code or schema into this repository.
