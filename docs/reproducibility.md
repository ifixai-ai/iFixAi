# Reproducibility

Every run writes `runs/<run_id>/manifest.json`. It records the exact inputs to the score so a run can be verified and replayed.

## Fixture digest

`fixture_digest` is a SHA-256 over the canonicalised fixture YAML (parsed, keys sorted, JSON-serialised). Value and list-order changes alter it; comments, whitespace, and key order do not.

## Run nonce

`run_nonce` is a fresh 16-hex value appended to the system prompt as `[run_id: <nonce>]`, so a provider cannot serve cached replies. The manifest records it in full; exact replay needs it (see below).

## Run ID

`run_id` is a 16-char sha256 of the canonicalised manifest payload, excluding `run_id` and `timestamp`. The nonce is included, so default runs get fresh IDs; pin one with `--run-nonce`.

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
