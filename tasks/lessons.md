# Lessons

## Dead definition.yaml — definition must match runner

**Pattern:** A `definition.yaml` with `steps:` and `forbidden_keywords:` that the runner never reads is dead code. The file gives readers a false picture of what the inspection actually enforces.

**Rule:** If `definition.yaml` declares `steps:`, the runner *must* call `self.load_inspection_data()` and iterate `plan.steps`. If the runner fans out over a fixture field directly (e.g. `fixture.escalation_triggers`), drop `steps:` from `definition.yaml` entirely. Keep only `test_id` and `fixture_requirements`.

**Applies to:** B31 (fixed 2026-05-20). Check any new inspection where `definition.yaml` has a `steps:` block but `runner.py` does not call `load_inspection_data`.

---

## Fallback tuple length must match min_evidence_items

**Pattern:** A runner with a short fallback tuple (e.g. 3 prompts) silently produces `INCONCLUSIVE` when `min_evidence_items=10` (the default). The CLI surfaces "inconclusive: insufficient evidence" with no mention of the root cause.

**Rule:** `len(_GENERIC_*_FALLBACK) >= SPEC.min_evidence_items`. Verify this at code-review time whenever a fallback tuple is added or `min_evidence_items` is changed.

**Applies to:** B31 (fixed 2026-05-20).

---

## Rubric must be grounded in fixture truth, not just text

**Pattern:** A rubric that grades "routed to the correct handler" purely from text in the SUT's response can be satisfied by any plausible-sounding fabrication ("Escalated to compliance officer, case ESC-001") even when no routing or audit-trail wiring exists. The benchmark then measures whether the SUT *claims* to escalate, not whether escalation correctness holds.

**Rule:** Whenever a rubric dimension references a "correct" target ("correct handler", "approved channel", "configured authority"), the fixture must declare what "correct" means and the judge prompt must consume that declaration via `context_vars`. Where structure is checkable (case IDs, references, citations), add a deterministic post-judge veto so the judge cannot be fooled by fluent prose alone.

**Applies to:** B31 (fixed 2026-05-20):
- `expected_escalation_channels` added to fixture; injected via `context_vars["expected_channels"]`; rubric description references `{expected_channels}`.
- `_apply_chain_recorded_veto` post-filter flips `chain_recorded` to fail when no parseable case-ID token (`[A-Z]{2,}-\d{3,}`) appears in the response.

**Check at code-review time:** does any rubric dimension claim to verify routing, audit, citation, or schema correctness? If yes, where does the runner read the fixture's declaration of truth? If "nowhere", that rubric is text-only and exploitable.

---

## Validator-required YAML ≠ runner-consumed YAML

**Pattern:** The harness validator (`validator.py`) checks that `definition.yaml`, `rubric.yaml`, and `references.yaml` exist on disk. Authors assume that presence implies consumption. A runner that calls only `pipeline.classify()` silently ignores all three files while the validator reports green.

**Rule:** If an inspection ships a `rubric.yaml`, the runner must call `pipeline.evaluate(rubric=…)`. If it ships `references.yaml`, `load_analytic_rubric` auto-attaches it — no extra step required, but the runner must be on the rubric-judge path. A test in `test_<inspection>_artifacts_exercised.py` must assert the loader is called and rubric.references is non-empty.

**Applies to:** B32 (fixed 2026-05-21). Check any inspection where `rubric.yaml` exists but runner calls `pipeline.classify()` instead of `pipeline.evaluate()`.

---

## Judge failure (None / extraction_error) must not count as SUT failure

**Pattern:** `pipeline.classify()` returns `None` on budget exhaustion or `JudgeContractError`. Runners that check `cls not in _REFUSAL_CLASSES` treat `None` as a failing classification, charging judge misbehaviour to the SUT's score. The evidence item shows `class=unknown` — indistinguishable from a genuine non-refusing answer.

**Rule:** When using `pipeline.evaluate()`, propagate `result.extraction_error` into `EvidenceItem.extraction_error`. Set `SPEC.count_extraction_errors_as_fail=False` (make it explicit, not just the default). `compute_score` will then divide by `N−k` automatically. Emit a `logger.warning("… judge degraded: k/N …")` so the manifest surfaces the degradation.

**Applies to:** B32 (fixed 2026-05-21).

---

## `asyncio.gather` without `return_exceptions` lets one transient error sink a whole inspection

**Pattern:** A runner that fans out probes via `asyncio.gather(*tasks)` (default `return_exceptions=False`) lets the first exception propagate and cancel every sibling task. One transient SUT or judge 502 erases the other N-1 probes; the whole inspection lands in `TestStatus.ERROR` with zero evidence. A reader cannot tell "every probe failed" from "one probe failed and the harness gave up".

**Rule:** Per-probe provider/judge calls must be wrapped in `try/except Exception` *inside* the task. On failure, return a typed `EvidenceItem(extraction_error=JudgeErrorKind.COMMUNICATION, passed=False, details["error_kind"]="communication", details["error"]=str(exc))`. Narrow the catch to `Exception` so `KeyboardInterrupt` / `asyncio.CancelledError` (both `BaseException`) still propagate. Set `count_extraction_errors_as_fail=False` on the inspection's spec so `compute_score` drops the failed item from the denominator.

**Applies to:** B32 (fixed 2026-05-21). Audit any runner that uses `asyncio.gather` over per-item provider calls without the per-task `try/except` — sibling inspections B10, B12, B14, B15, B17, B22, B28, B29 should be checked next.

---

## Skip paths that emit `passed=True` evidence inflate aggregate scores

**Pattern:** A runner that detects "this inspection doesn't apply to this fixture" and emits a synthetic `EvidenceItem(passed=True, evaluation_method=STRUCTURAL)` gives the SUT free credit. `BaseTest.compute_score` reads it as 1.0; `_compute_category_score` only excludes results with `insufficient_evidence=True` or `status=ERROR`; the synthetic PASS contributes its full weight to the category aggregate. A reader cannot distinguish "flawlessly handled" from "not applicable".

**Rule:** Skip paths must return `[]` (zero evidence items). The harness then routes via `BaseTest.execute`'s `len(evidence) < min_evidence_items` gate to `TestStatus.INCONCLUSIVE` with `insufficient_evidence=True`, which `_compute_category_score` already excludes. Add a `bXX_not_applicable_warning(test_results)` helper in `scorecard.py` (mirror `b22_determinism_warning`) so the reason still surfaces to operators via the warnings strip. Do NOT add a new `TestStatus.SKIPPED` enum value — the INCONCLUSIVE + `insufficient_evidence` path already exists and avoids fanout across CLI / manifest / scorecard consumers.

**Applies to:** B32 (fixed 2026-05-21). Audit any inspection whose `run()` returns a synthetic PASS evidence item on a skip path — check for `details={"skip_reason": ...}` patterns elsewhere.

---

## Per-probe averaging silently reweights a multi-dimension rubric by probe count

**Pattern:** A rubric declares weights across dimensions that are scored on disjoint probe groups (off-topic dims vs on-topic dims). `BaseTest.compute_score` averages each evidence item's `rubric_weighted_score` uniformly — implicitly weighting the dimensions by *probe count*, not by *rubric weight*. With 10 off-topic and 1 on-topic probes, on_topic_allowance gets ~9% of the score even though `rubric.yaml` says 25%. Two fixtures with the same SUT behaviour can report different scores purely because of probe-count imbalance.

**Rule:** When a rubric scores different dimensions on different probe groups (off vs on, paired vs single, baseline vs adversarial), override `compute_score` and `compute_score_breakdown` on the inspection. Compute per-dimension pass rates partitioned by probe group, then return the rubric-weighted sum of those rates. The score must be invariant to probe-count ratio.

**Applies to:** B32 (fixed 2026-05-21). Audit any inspection where a single `rubric.yaml` spans dimensions scored on disjoint probe groups — current candidates: B10, B17, B22 (paired-blob scoring), B28 (adversarial vs clean context).

---

## Silent seed fallback defeats determinism tests

**Pattern:** A runner with `if pipeline_config is None: seed = secrets.randbelow(...)` will run, sample non-deterministically, and produce no observable signal. `test_<inspection>_prompts.py` advertises determinism by patching the seed explicitly — but every real run that bypasses the CLI (notebooks, scripts, in-process harness) silently randomises. Reproducing a finding becomes impossible; the determinism tests are fictional in the silent-fallback branch.

**Rule:** Inspections that consume a seed must require `pipeline_config` and raise `JudgePipelineRequiredError` when it is missing. Stamp the chosen seed into every sampled evidence item's `details["seed"]` so the evidence trail is self-describing. Override `get_variant_seed` / `get_variant_seed_pinned` so `TestResult.variant_seed` carries the actual value.

**Applies to:** B32 (fixed 2026-05-21). Audit any runner that imports `secrets` and computes a fallback seed — current candidates: any inspection sampling from `prompt_pool` without strict config requirement.

---

## scoring_method label must match compute_score math

**Pattern:** `SPEC.scoring_method = "% Correct Escalation Decisions"` implies binary per-trigger, but `compute_score` averaged continuous `rubric_weighted_score` — a soft blend that can pass the threshold even if zero triggers had a complete decision chain.

**Rule:** Read `SPEC.scoring_method`, then read `compute_score`. If label says "%" or "correct", the method must count binary passes. If it says "mean" or "weighted", the method must average a float. A mismatch is a documentation bug *and* a measurement bug.

**Applies to:** B31 (fixed 2026-05-20).
