# Reproducibility

`ifixai` records every run against a content-addressed manifest (`runs/<run_id>/manifest.json`) that captures the exact inputs to the score so an auditor can reproduce the run later. This document specifies how the manifest's content-addressability works, what it does and does not guarantee, and how to replay a run.

## The fixture digest

The `fixture_digest` pins the fixture's *semantic* content, not its bytes — so an auditor can detect any value change while comment, whitespace, and key-order edits are ignored. It is a SHA-256 hex string over the canonicalised YAML. The canonicalisation is:

1. Read the fixture file as UTF-8.
2. Parse it via `yaml.safe_load` (discards comments, normalises whitespace).
3. Recursively sort dictionary keys at every nesting level. List order is preserved.
4. Serialise via `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
5. Encode UTF-8 and take `hashlib.sha256(...).hexdigest()`.

This produces a digest that is:

- Stable across YAML comment edits.
- Stable across YAML whitespace and key-order edits.
- Stable across equivalent scalar representations (e.g. `1` and `1.0` are still distinct scalars since we do not canonicalise number types beyond what `yaml.safe_load` returns).
- Sensitive to list order, since lists in our fixtures are semantically ordered (e.g. step sequences).
- Sensitive to any value change.

The algorithm is pinned. Any future change is a breaking change to the manifest format and requires a new schema version.

The all-zero sentinel (`"0" * 64`) is rejected at model-validation time. If you see it in a manifest, that manifest is not reproducible — regenerate.

## The run nonce

The `run_nonce` defeats a hostile provider that caches `(prompt_hash) → canned reply` under deterministic mode: a fresh 16-hex value (from `secrets.token_hex(8)`) is mixed into the SUT system prompt every run, so a hash-keyed cache can't match across runs.

The nonce is appended on its own line to the SUT system prompt as `[run_id: <nonce>]` immediately before sending. It is recorded in the manifest **in full** — not redacted — because exact replay requires the original value.

To enable exact replay against a deterministic provider, pass the recorded value:

```bash
ifixai run ... --run-nonce <16-hex-from-prior-manifest>
```

`ChatProvider.replay_protected` (default `True`) is a self-reported flag a provider can flip to `False` to advertise that it does not implement replay protection. The harness uses this only as a signal in the manifest and scorecard; the run-level nonce is applied unconditionally.

## The run ID

`run_id` is a 16-char sha256 hex of the manifest's canonicalised payload, excluding `run_id` itself and `timestamp`. The payload includes both `schema_version` and `run_nonce`, so two default runs with otherwise identical inputs produce **different** `run_id`s by design (the nonce is fresh per run). Pass `--run-nonce` to pin the value if exact replay against a deterministic provider is required.

## Masked non-deterministic fields

Byte-identity across replay assertions excludes the following fields, which are expected to differ between runs and are not load-bearing for the score:

- `manifest.timestamp`
- `scorecard.generated_at`
- `scorecard.runtime_seconds`
- Per-inspection `latency_ms`, `started_at`, `completed_at`

## What reproducibility does NOT promise

Reproducibility here means byte-identical *replay* against a deterministic provider — not stable scores against live LLMs, and not stability when you change the judge set or upgrade a pinned hash.

- **Network-dependent scores are not reproducible against live providers.** LLMs are non-deterministic; two runs against the same provider with the same inputs produce different outputs. To verify bit-identical replay you need a deterministic provider that returns a pre-recorded response table.
- **Reproducibility is conditional on the judge set.** Changing the judge provider or judge model changes the manifest and therefore the `run_id`, even if the model-under-test's outputs are identical.
- **Rubric hashes, test versions, and the normaliser version are all pinned.** Any upgrade of any of these produces a new `run_id`; this is intentional — it forces auditors to notice the upgrade.

## Manifest schema versioning

The manifest carries a `schema_version` integer. The current version is **2** (H4: run-nonce). Version **1** manifests predate the nonce field; `load_manifest()` accepts them, emits a `DeprecationWarning`, and synthesises `run_nonce=""`. `verify_run_id()` is version-aware: for a v1 manifest it recomputes the legacy payload (without `schema_version` and `run_nonce`) so the stored `run_id` still verifies. v1 manifests do not carry replay protection — regenerate them to enable it.

## Replaying a run

Replay a run through the internal manifest API: load the manifest, verify the `run_id` and `fixture_digest`, then re-run with the recorded nonce. There is no dedicated `replay` CLI command yet.

```python
from ifixai.evaluation.manifest import load_manifest, verify_run_id
from ifixai.utils.fixture_digest import verify_fixture_digest

manifest = load_manifest(Path("runs/<run_id>/manifest.json"))
assert verify_run_id(manifest), "manifest has been tampered with"
assert verify_fixture_digest(fixture_path, manifest.fixture_digest), "fixture has been edited"
```

Re-run with the same nonce to reproduce against a deterministic provider:

```bash
ifixai run ... --run-nonce $(jq -r .run_nonce runs/<run_id>/manifest.json)
```

With a deterministic provider that returns a pre-recorded response table, re-running against the same manifest produces a byte-identical scorecard modulo the masked fields listed above.
