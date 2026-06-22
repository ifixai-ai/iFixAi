# What's left to do

Plain-language steps to finish the iFixAi Claude plugin. Tags:
**[done]**, **[partly done]**, **[buildable now]**, **[needs a real Claude install]**,
**[release work]**.

## Already done (engine + plumbing + safety + UX)

- Engine fixes: the "everything grades D" cap bug (R14) and free-text safety rules (R9).
- The bridge and its transports (live, record, replay, resume, stub, ensemble).
- Multi-check runner with markdown + **CLI-matching HTML** report and coverage labels.
- Multiple graders (ensemble); the auto-builder (usage profile → test setup).
- The settings reader that makes a structural check assessable.
- **Safety fence:** live runs in a throwaway sandbox, never auto-approve risky
  actions, no real tools without an explicit sandbox.
- **Grade-stability labels, cost presets with a consent gate, the confirm summary,
  and a verified full 26-inspection run.**
- **The `--profile` front door**: the skill's discovery output (a small JSON) →
  confirm summary → generated fixture → run, no YAML authoring.
- **Both audiences covered by the same flow**: the skill has two discovery
  paths — a developer's repo (CLAUDE.md, custom agents, MCP tools) and a simple
  user's connected apps (Cowork personal assistant) — and the personal-assistant
  profile is tested end-to-end (schema-valid, risky connectors restricted, B08
  evidence floor cleared).
- **API billing as an alternative to the plan**: `--mode api` runs SUT + judge on
  the engine's native Anthropic provider (`ANTHROPIC_API_KEY` from the env, never
  the command line); `--judge-models` exposes ensemble grading in both live modes.
- **Deep-evaluation hardening pass**: every live run gates on `--yes` with a
  mode-correct cost line; offline runs are bannered "NOT a diagnostic" and say
  "nothing billed"; the quick preset includes all mandatory minimums (it could
  previously never grade above D); progress streams one line per inspection;
  headless runs auto-checkpoint and **resume without re-billing**; test ids are
  validated; `--settings` makes B02 (the deterministic control plane) assessable;
  single-entry `--judge-models` is honored; the probed process gets an explicit
  tool deny-list that overrides the user's own allowlists; `profile.py` →
  `usage_profile.py` (it shadowed Python's stdlib `profile` module).
- **Deep-review hardening (round 2)**: every billable run names the payer (not
  just preset runs); headless pins a cheap default model; api reports the engine
  default rather than "(account default)"; offline runs and HTML are bannered
  "NOT a diagnostic"; a high grade surfaces any below-threshold / capped /
  errored inspections; replay refuses model flags and out-of-recording tests;
  the spike is offline-only (no ungated billing); checkpoints are atomic and
  per-run namespaced; containment claims are mode-conditional and accurate.
- 144 automated tests pass (1 api test skips without the SDK); lint clean. The
  `tests/` suite is now version-controlled and gates both CI and the PyPI release.

---

## Group A — before anyone tests for real

**Step 1. Prove the live connection on a real machine.** **[done on macOS / scale + other surfaces open]**
Done on Claude Code (macOS): a `claude -p` subprocess inherits auth, the SUT+judge
round-trip runs on real models, scoring + scorecard render, the sandbox is cleaned
up, and the cost-guard/checkpoint/below-threshold paths all behaved live. Cost note:
the account default was a 1M-context Opus (~$0.20/call), so the orchestrator now
pins Sonnet by default. **Still open:** per-call latency and plan rate-limits at
full concurrency over hundreds of calls, other install types (desktop/IDE/web),
and Cowork.

**Step 2. The "read my setup and confirm" front door.** **[partly done]**
- The confirm summary (`describe_profile`), the profile → test-setup builder,
  and the executable path (skill writes a profile JSON → `--profile` shows the
  confirm screen and generates the fixture) are **done**; `SKILL.md` now spells
  out what to read (CLAUDE.md, settings, MCP configs) and what to ask.
- Still needed: validate that discovery-in-a-live-session fills the profile
  well (needs the live environment).

**Step 3. Make the skill launch the engine.** **[needs a real Claude install]**
The run-flow instructions are written in `SKILL.md`; actually launching the engine
from the skill can only be tested inside real Claude.

**Step 4. The advanced "real tools, blocked actions" mode.** **[partly done / needs live]**
The safe default is done and now enforced two ways: throwaway folder + an explicit
deny-list (`--disallowedTools` for shell/file/web tools) that overrides the user's
own pre-approved allowlists. The opt-in mode that lets the assistant use real tools
in a sandbox still needs the live CLI to confirm flag behaviour (R11).

---

## Group B — make the results trustworthy

**Step 5. Smooth out grade wobble.** **[partly done]**
Borderline grades are now **flagged** (distance to the nearest boundary + a "this
could shift, verdicts are sampled" note). The deeper fix — automatically
re-sampling the deciding verdicts a few times near a boundary — needs live judges,
so it's not finishable offline. (Ensemble/Full mode already reduces wobble.)

**Step 6. Show a cost warning before starting.** **[done]**
Every billable run (headless/api, preset or not) shows a mode-correct estimate and
refuses to start without `--yes`; offline runs say "nothing billed". Headless runs
checkpoint as they go and resume without re-billing; api runs start over and the
estimate says so.

**Step 7. Run the full check set and tune the setups.** **[done]**
The full 26-inspection set runs end-to-end; every pillar populates and coverage is
labelled. (Under the canned stub, B30's generic verdict doesn't fit its rubric so
it reads 0.0 — a stub-data limitation, graded properly by a real judge.)

**Step 8. Faithful back-and-forth conversations.** **[partly done / needs live]**
The structure is in place; replacing the flattened-into-one-message live path with
true turn-by-turn conversation needs a live check to confirm fidelity.

---

## Group C — ship it to other people

**Step 9. Make it installable as a Claude Code plugin.** **[done — no binary needed]**
The blessed pattern replaces the binary-bundling plan: the engine ships on PyPI
(`ifixai`, with the `ifixai-diagnose` console entry point), and a `SessionStart`
hook (`plugin/hooks/bootstrap.sh`) provisions it into `${CLAUDE_PLUGIN_DATA}/venv`
on first run. Verified: the wheel bundles `ifixai/plugin/*` + its data, installs
into a clean venv with no repo present, and `ifixai-diagnose --mode replay` runs;
`bootstrap.sh` provisions + is idempotent. The plugin dir is pure assets
(`plugin/.claude-plugin/plugin.json`, `skills/`, `hooks/`), and the repo is its own
marketplace (`.claude-plugin/marketplace.json`). `claude plugin validate` passes
for both.

**Step 10. Publish it.** **[release work — gated on Workstream G]**
Tag a `v3.0.2` release (release.yml publishes `ifixai` to PyPI, now **gated on the
test suite**), then submit `plugin/` to a marketplace with the honest
"runs adversarial prompts" notice already in the manifest/SKILL. The engine
version the plugin pins (`plugin/requirements.txt` → `ifixai[anthropic]==3.0.2`)
must exist on PyPI before end users can install.

---

## Workstream G — live re-validation (required before the "validated" claim)

The safety/correctness hardening below was implemented and is offline-tested, but
the live `claude -p` behaviour it depends on has NOT been re-validated. Run these
**on a throwaway account with no connected apps and no real secrets**, then flip
the status lines in `plugin/README.md` / `SKILL.md` / `plugin.json` to "validated":

1. **Containment (deny-all).** Confirm `--disallowedTools *` actually removes every
   tool — including MCP/connectors — from a `claude -p` SUT, overriding a settings
   `permissions.allow`. Try to get the SUT to call a tool; confirm it cannot.
2. **SUT platform-refusal → INCONCLUSIVE.** Run B12 over `--mode headless`; confirm
   a Usage-Policy wall on the SUT scores INCONCLUSIVE (not FAIL), and the same over
   `--mode api` actually grades.
3. **Refusal-vs-error classification.** Capture real `claude -p` `is_error`/
   `stop_reason` envelopes; confirm `parse_output` treats refusals as responses and
   real transient errors as retryable (not mis-classified).
4. **Both live paths end-to-end.** **[partly done]** A single-inspection `headless`
   run (B16) on real Sonnet is confirmed live — auth inheritance, SUT+judge
   round-trip, scoring, markdown+HTML scorecard, disposable sandbox, checkpoint
   cleared, and the grade-honesty/mandatory-minimum gate all behaved (B16 PASS at
   96.7%, even surfacing a real silent-failure finding). Still open: a full
   `standard` run on `headless` AND on `api`, plus checkpoint-resume (interrupt +
   rerun) and rate-limit behaviour at the 3-wide cap. Do not generalize from the
   single B16 run.
5. **Cost accuracy.** Compare the `~600`/`~1500+` call + cost bands against a real
   bill; correct the preset bands in `orchestrator.py` if off.
6. **Plugin install on real Claude Code.** `/plugin marketplace add` →
   `/plugin install` → SessionStart provisions the venv → `/ifixai:ifixai` runs
   `ifixai-diagnose` from `${CLAUDE_PLUGIN_DATA}`. Confirm the env vars resolve in
   the skill's Bash context.

---

## Deferred (post-v1, low-value/high-risk to change now)

- **api-mode checkpoint/resume.** Today an interrupted api run starts over; the
  consent screen warns loudly. Adding resume means routing api through the bridge's
  caching seam — a billing-path change best done with live coverage.
- **Nonce-normalization precision.** `replay_key` normalizes any 16-hex run; a
  prompt containing a bare 16-hex id could collide. Low frequency (the nonces are
  the only 16-hex content in practice); anchoring risks the golden-parity anchor.

---

## Where things stand

The **trust blockers are fixed and offline-verified** (125 tests, CI- and
release-gated): the plugin is genuinely installable (PyPI engine + provisioning
hook, proven in a clean venv), the fence denies all tools incl. connectors, SUT
platform-refusals no longer manufacture false FAILs, the consent screen names the
account-level risk, and the status claims are consistent. What remains is
**Workstream G** (live re-validation on a throwaway account) and the **release
tag** (publish `ifixai` 3.0.2 + submit the plugin). Cowork/desktop/web surfaces
stay out of scope for v1.
