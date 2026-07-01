# Report redesign: scorecard / audit / JSON

## Problem

Each run dumps everything into one 3,000-line markdown and one ~552KB JSON. The
JSON is 99% raw evidence that no consumer reads (`compare.py` and the HTML diff
read only scores and verdicts). The full markdown renders the same evidence
twice, ~1,500 lines apart. The good short report already exists as
`-summary.md` but is buried as a third file.

## Target

Three files, each with one job.

| File | Reader | Contents | Size |
|---|---|---|---|
| `ifixai-<sys>-<fixture>.md` | operator, buyer, governance | severity-first scorecard, no evidence | ~100 lines |
| `ifixai-<sys>-<fixture>-audit.md` | auditor, compliance, deep-dive | full evidence, failures first | large, not the default read |
| `ifixai-<sys>-<fixture>.json` | CI, compare, HTML diff | thin machine contract, no evidence | ~10KB (from 552KB) |

The live console and interactive HTML artifact stay as they are; the files stop
duplicating them.

## Plan

0. **Severity primitive.** Add `derive_severity(br, result)` to `scorecard.py`
   (CRITICAL = mandatory-minimum violation, HIGH = failed scored test,
   MEDIUM = advisory / consistency-capped, INFO = exploratory / attestation).
   One function, shared by all three files. Unit test it.
1. **Scorecard.md.** Repurpose `generate_summary_report` into
   `generate_scorecard_report`: header, verdict, mandatory minimums, footer,
   plus a severity histogram, a one-line warnings strip, a compliance strip,
   and an action plan (replaces `_render_top_failures`, sorted by severity then
   threshold gap, each failure points to the audit file). Category table shows
   scored rows only.
2. **Audit.md.** Add `generate_audit_report`: provenance header, verdict recap,
   control-level regulatory section, and one merged evidence block per
   inspection (`render_inspection_detail`), failures first, secret-scrubbed,
   uncapped. Default to all inspections. Delete `generate_markdown_report` and
   its orphaned renderers.
3. **Thin JSON.** Rewrite `generate_json_report`: drop the evidence blob, add
   per-test `severity` and a top-level `action_plan`, lift regulatory to
   control-level. Keep `score_pct` (avoids the `artifact.py:157` diff bug) and
   flat `ci_lower/ci_upper`; drop the nested CI object. `compare.py` is
   unchanged; add a regression test.
4. **Wire up.** `save_reports` writes `{base}.md` + `{base}-audit.md` for
   markdown/both and `{base}.json` for json/both. Update echo labels.
   `FORMAT_CHOICES` and the `both` default stay.
5. **Tests + docs.** Update assertions to the new artifact set, keep the suite
   green, repoint the README "source of truth for CI" at the thin JSON and add
   the audit file to the output table.

## Defaults (change on request)

- Audit md carries all evidence, failures first (not failures-only).
- Compliance control table lives in the audit md.
- `score_pct` stays in the JSON rather than patching `artifact.py`.

## For the team

Each run currently produces one huge markdown and one huge JSON, mostly raw
evidence nobody reads. We split the output into three, each with one job: a
short scorecard you actually read (grade, what failed, what to fix first), an
audit file with the full evidence for compliance, and a slim JSON for CI. Same
data, sized for its reader.
