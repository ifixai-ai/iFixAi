# Worked examples

These are the two worked examples from [the report spec](../../../docs/register-spec.md), and they
double as the fixture the test suite and CI validate against — so an example a submitter is told to
copy cannot quietly stop being a valid submission.

The register itself lives at
[ifixai-ai/agent-misbehavior-registry](https://github.com/ifixai-ai/agent-misbehavior-registry),
where these same two directories sit at the repository root.

# The layout

Reproducible reports of AI-agent misbehaviour. Two directories, both plain YAML in git:

| | what it holds |
| --- | --- |
| [`entries/`](entries/) | **accepted** entries. Curated, each with a permanent `AGENT-YYYY-NNNN` id that is never removed or reused. |
| [`candidates/`](candidates/) | **submissions**. Anyone may file; each carries a derived `CAND-<digest12>-<YYYYMMDD>` reference and claims no permanent id. |

- The format, and why each required field is required: [`docs/register-spec.md`](../../../docs/register-spec.md)
- How to file one: [`docs/register-contributing.md`](../../../docs/register-contributing.md)
- The schema: [`ifixai/schemas/misbehaviour-report-1.schema.json`](../../schemas/misbehaviour-report-1.schema.json)

Check the whole register with a clone and no network:

```bash
ifixai report validate --register-root ifixai/register/examples \
  ifixai/register/examples/entries/*.yaml ifixai/register/examples/candidates/*.yaml
```

The format is runtime-neutral: a report describes the world, the identities, the tools, the request,
the expected final world state and the grading checks in its own terms, and binds that description
to one or more runtimes under `reproductions`. Harbor is the reference implementation, not the
definition.
