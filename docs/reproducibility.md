# Reproducibility

Every run writes `runs/<run_id>/manifest.json`. It records the exact inputs to the score so a run can be verified and replayed.

## Fixture digest

`fixture_digest` is a SHA-256 over the canonicalised fixture YAML (parsed, keys sorted, JSON-serialised). Value and list-order changes alter it; comments, whitespace, and key order do not.

YAML dates and timestamps in metadata use ISO strings during canonicalisation, compatible with the fixture model's existing JSON export representation.

## Run nonce

`run_nonce` is a fresh 16-hex value appended to the system prompt as `[run_id: <nonce>]`, so a provider cannot serve cached replies. The manifest records it in full; exact replay needs it (see below).

## Run ID

`run_id` is a 16-char sha256 of the canonicalised manifest payload, excluding `run_id` and `timestamp`. The nonce is included, so default runs get fresh IDs; pin one with `--run-nonce`.

## SUT context and resuming

Schema v4 records `sut_context_digest`, a SHA-256 of the effective system prompt
and deployment endpoint. It also includes the requested SUT sampling temperature
and seed in the run identity. Changing these inputs must start a fresh run rather
than reusing completed inspections against a different configuration. The prompt
and endpoint are hashed, not stored in plaintext in the manifest; API keys and
extra authentication headers are not inputs to this digest.

For HTTP, the resolved default endpoint is included and trailing slashes are
normalized exactly as the adapter normalizes them. This detects configuration
changes, not an unversioned deployment changing behind an unchanged URL; use
`--system-version` to identify those deployments.

Historical manifests retain their original hash verification rules. They remain
readable, but `--resume` cannot establish their SUT context and asks for a fresh
run. New schema-v4 runs resume normally when the context is unchanged.

## Masked fields

Replay byte-identity checks ignore these; they vary between runs and do not affect the score: `manifest.timestamp`, `scorecard.generated_at`, `scorecard.runtime_seconds`, and per-inspection `latency_ms`, `started_at`, `completed_at`.

## What reproducibility does NOT promise

Byte-identical replay requires a deterministic provider with pre-recorded responses; live LLM scores are not reproducible. Changing the judge set, or upgrading pinned rubric, test, or normaliser versions, changes the `run_id` on purpose.

## Replaying a run

There is no `replay` command yet. Verify the manifest, then re-run with the recorded nonce:

```python
from ifixai.evaluation.manifest import load_manifest, verify_run_id
from ifixai.utils.fixture_digest import verify_fixture_digest

manifest = load_manifest(Path("runs/<run_id>/manifest.json"))
assert verify_run_id(manifest), "manifest has been tampered with"
assert verify_fixture_digest(fixture_path, manifest.fixture_digest), "fixture has been edited"
```

```bash
ifixai run ... --run-nonce $(jq -r .run_nonce runs/<run_id>/manifest.json)
```
