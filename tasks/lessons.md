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

## scoring_method label must match compute_score math

**Pattern:** `SPEC.scoring_method = "% Correct Escalation Decisions"` implies binary per-trigger, but `compute_score` averaged continuous `rubric_weighted_score` — a soft blend that can pass the threshold even if zero triggers had a complete decision chain.

**Rule:** Read `SPEC.scoring_method`, then read `compute_score`. If label says "%" or "correct", the method must count binary passes. If it says "mean" or "weighted", the method must average a float. A mismatch is a documentation bug *and* a measurement bug.

**Applies to:** B31 (fixed 2026-05-20).
