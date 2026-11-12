# The misbehaviour report spec, version 1

A **misbehaviour report** describes one kind of AI-agent misbehaviour precisely enough that a
stranger can reproduce it. It is to agent misbehaviour what a CVE record is to a software flaw: a
permanent, citable description of something that goes wrong, separate from any one proof of concept.

**The canonical specification is
[`spec/report-spec-1.md`](https://github.com/ifixai-ai/agent-misbehavior-registry/blob/main/spec/report-spec-1.md)
in the register**, where a citation lands. This page covers the implementation that lives here and
the decisions behind the format; it indexes the fields rather than restating them, so there is no
second copy to keep in step.

- Machine-readable schema: [`ifixai/schemas/misbehaviour-report-1.schema.json`](../ifixai/schemas/misbehaviour-report-1.schema.json), generated from the model and published to the register with its digest recorded
- The format in code: [`ifixai/register/spec.py`](../ifixai/register/spec.py)
- Worked examples: [`ifixai/register/examples/`](../ifixai/register/examples/) — an accepted entry and a candidate, which double as the test fixture
- How to file one: [register-contributing.md](register-contributing.md)
- Where entries live: [ifixai-ai/agent-misbehavior-registry](https://github.com/ifixai-ai/agent-misbehavior-registry)

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

## The fields

The field-by-field reference — every required field, the exact predicates, and the argument for why
omitting each one breaks reproduction — is the **canonical specification**, authored in the register
itself:

**→ [`spec/report-spec-1.md`](https://github.com/ifixai-ai/agent-misbehavior-registry/blob/main/spec/report-spec-1.md)**

It lives there because that is the page a citation of the register lands on, and because the
definition should not depend on this repository continuing to exist. This page is about the
implementation and the decisions behind it. The table below is an index, deliberately carrying no
reasoning of its own so that it cannot drift from the specification in substance.

| | Field | In one line |
| --- | --- | --- |
| 1 | `world` | Entities and records with concrete ids and field values, not prose. |
| 2 | `identity` | Roles, which role holds which tool, escalation paths. `identity_source` is fixed to `login`. |
| 3 | `tools` | Name, signature, declared risk, whether it mutates state. |
| 4 | `request` | The verbatim text, and which login sent it. |
| 5 | `expected` | A condition on the final world state. Never on the wording of a reply. |
| 6 | `twin` | The identical request about the same record, from a role that holds the tool. |
| 7 | `grading` | Named 0/1 checks over world state or the evidence log. No LLM judge. |
| 8 | `reproductions[].calibration` | The reference solution scores 1; doing nothing scores 0. |
| 9 | `determinism` | Seed, temperature, step cap, and what is allowed to vary. |
| 10 | `observed` | Model and harness with versions, occurrences out of trials, date. |
| 11 | `provenance` | Synthetic or audit-derived; de-identified and rights-cleared flags. |

Also captured, because a finding that cannot be told apart from its neighbour gets argued about
rather than fixed: the exact model snapshot the provider served and whether it came through a
gateway; system-prompt and tool-schema hashes; which guardrails were active; the anatomy of the
failure, including whether the agent acknowledged the boundary before crossing it; the rate at each
variant rung as a dose-response curve; the twin's over-refusal rate beside every violation rate; and
the models tested that did **not** show the behaviour.

The machine-checkable statement of all of it is
[`ifixai/register/spec.py`](../ifixai/register/spec.py), from which
[the JSON Schema](../ifixai/schemas/misbehaviour-report-1.schema.json) is generated.

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
| Identifier | `CAND-<digest12>-<YYYYMMDD>`, derived | `AGENT-YYYY-NNNN`, permanent, never reused |
| Lives in | `ifixai/register/examples/candidates/` | `ifixai/register/examples/entries/` |
| Means | somebody filed this and it passes the validator | a curator has read it and stands behind it |

**Anything submitted is a candidate.** A submission that claimed an `AGENT` id is rejected by the
validator. Only a curated entry consumes a permanent id, and an id, once issued, is never removed or
reused — a mistaken entry becomes `rejected` and keeps its id, as a rejected CVE does.

This is what lets the register take volume without diluting its own meaning: the quality signal is
the `accepted` set, and the candidate set can be as large and as uneven as the world is.

## Identifiers

An accepted entry gets `AGENT-YYYY-NNNN` — `AGENT-2026-0001`. It means agent. It is not an acronym
and has no expansion.

> The prefix deliberately carries no vendor's name. CVE ids are not `MITRE-YYYY-NNNN`, and an
> identifier that names one company weakens a register meant to be cited by people who do not work
> there.

A candidate carries `CAND-<digest12>-<YYYYMMDD>`, derived from its behaviour signature and creation
date, so two people can file the same behaviour without colliding and nothing has to be allocated.

Both prefixes, and the register's location, are single constants in
[`ifixai/register/naming.py`](../ifixai/register/naming.py). Changing one is a one-line change plus a
schema regeneration; ids already issued keep the prefix they were issued under.

## Safety: what the format cannot do on its own

The format has one hard constraint around it, implemented in the contribution path and not
negotiable: **no submission leaves a machine without a person approving that specific submission.**

De-identification runs locally **before** the question, so the thing being agreed to is the redacted
thing and the summary of what was replaced is on the screen when the person answers. The question is
one `[y/N]`, defaulting to No. An approval is bound to the exact bytes it approved, so an edit
afterwards invalidates it. An agent may *prepare* a submission and cannot make one. There is no
configuration flag that removes the human.

Publication **rights** are a separate question, and the one thing no tool can check: only the
organisation that owns an audited system can say whether its agreements permit publishing a finding
from it. Asking per submission is correct and also empties the register, because nobody stops
mid-debugging to read a contract. So an organisation answers it once, in a committed
`.ifixai/contribution.yaml` — who authorised it, what scope, when it expires — and a submission
records which authorisation it relied on. That is an auditable trail rather than a checkbox. It
removes the rights question, never the yes.

See [register-contributing.md](register-contributing.md) for the path itself.

## Keeping one source for each artefact

Two statements of one format drift, so neither artefact has two homes:

| Artefact | Source | How drift is caught |
| --- | --- | --- |
| The JSON Schema | generated from `ifixai/register/spec.py` | `scripts/generate_register_schema.py --check` in this repo's CI; the register checks its published copy against the sha256 in its `schema/PROVENANCE.md` |
| The specification prose | authored in the register | nothing to catch: this page indexes it and does not restate it |
| The worked examples | `ifixai/register/examples/` here, published to the register | validated by the same checks a submitter runs, in both repos |

`scripts/publish_register_spec.py --registry <clone>` republishes the schema and its provenance
note; `--check` reports drift and writes nothing.

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
