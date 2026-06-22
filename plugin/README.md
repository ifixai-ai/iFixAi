# iFixAi Claude plugin

Claude is the **operator/guide** here, not the thing under test: it reads the
user's agent setup, builds a fixture, runs the diagnostic, and explains the
scorecard. The two model-call seams — **the agent under test** and **the
judge(s)** — run on real provider APIs the user chooses (Anthropic, OpenAI,
Gemini, Azure, Bedrock, …; the same provider for both or different ones), each
billed to that provider's own account via a key set in the user's environment.
Inspection selection, judge-verdict parsing, and scoring stay in the unmodified
iFixAi engine.

## Status — developer preview (offline-verified; live re-validation pending)

Everything is exercised on the **record/replay + stub substrate** (deterministic,
model-free) and passes in CI (the test suite gates the PyPI release). The live
path runs the agent under test and the judge(s) on real provider APIs
(`--mode api`, any provider), with every credential read from the environment and
a missing one failing fast — verified by `tests/test_multiprovider_and_artifact.py`
and `tests/test_phase2_orchestrator.py`. **Live end-to-end runs against paid
provider accounts (latency, rate-limits, provider-side refusals at scale) have NOT
yet been broadly re-validated** — that is Workstream G in `TODO.md`. Until then,
run live only against a throwaway key with no real secrets.

**Install gate (read before listing this plugin).** A clean `/plugin install`
provisions engine `ifixai[anthropic]==3.0.2` (`requirements.txt`) from PyPI on the
first session — but **that version is not published to PyPI yet**, so until it
ships a clean install fails at provisioning and the only working path is the
local-engine override (`IFIXAI_ENGINE_SPEC`; see [Install](#install-as-a-claude-code-plugin)).
Publishing 3.0.2 is the explicit release gate (TODO Step 10 / Workstream G item 6).
Other-provider SUT/judge runs also need that provider's SDK in the venv — install
its extra on demand (`"${CLAUDE_PLUGIN_DATA}/venv/bin/pip" install "ifixai[openai]"`,
`gemini`, `azure`, …); the run pre-flights this at the consent screen and prints
the exact command if it's missing. Broad live validation across providers
(latency, rate-limits, provider-side refusals, full-set runs) remains open in
Workstream G.

**Data handling.** The profile JSON, the generated fixture (`ifixai-fixture.yaml`,
written beside the profile so the user can review and edit it), checkpoints, and
any recordings are local files. Nothing leaves the machine except the model calls
themselves; no keys are written to disk or passed on the command line.

| Area | What's built | Verified by |
|------|--------------|-------------|
| **Engine R14** | B01/P01 "no control plane" → not-applicable in the mandatory-minimum gate (was FAIL → capped every reduced run at D) | `tests/test_mandatory_minimum_na.py` |
| **Engine R9** | `policies.safety_rules[]` free-text rules → one B09 violation scenario each; no-op when absent | `tests/test_r9_free_text_rules.py` |
| **Phase 0** | Bridge loop (SUT + judge seams) on B09, real scoring, record/replay | `tests/test_phase0_bridge.py` |
| **Phase 1** | Checkpoint/resume caching transport (no re-billing on restart); seam-coverage (egress confined to two channels) | `tests/test_phase1_transport.py` |
| **Phase 2** | Multi-inspection orchestrator → real `TestRunResult` → markdown + HTML scorecard + coverage labels; golden parity | `tests/test_phase2_orchestrator.py` |
| **Phase 3** | Full mode: ensemble of distinct-model judges over the bridge; engine aggregation; divergence + determinism | `tests/test_phase3_ensemble.py` |
| **Phase 4** | Usage-derived fixture builder from a session profile (sized to clear floors); surface degradation tiers | `tests/test_phase4_profile.py` |
| **Phase 5** | Claude Code governance adapter (settings.json + hooks → `GovernanceArchitecture`); B02 INCONCLUSIVE → PASS — reachable via `--settings` | `tests/test_phase5_governance.py` |
| **Containment** | The agent under test is a bare provider API call with **no tools, connectors, or file access attached** — even when a probe tries to make it act, there is nothing to act with; it may echo tool-call syntax in its reply text, but nothing executes and nothing outside the run is read or written. The real control for adversarial traffic is a throwaway key with no real secrets. | env-only key resolution + `--mode api` (`tests/test_multiprovider_and_artifact.py`) |

## Layout

The Claude Code plugin (this directory) is **pure assets**; the Python engine it
drives ships in the `ifixai` PyPI package and is provisioned on first run.

```
plugin/                              # the installable Claude Code plugin
├── .claude-plugin/plugin.json       # manifest (skill + hooks auto-discovered)
├── skills/ifixai/SKILL.md           # the operator playbook (the front door)
├── hooks/hooks.json                 # SessionStart → bootstrap.sh
├── hooks/bootstrap.sh               # provisions ifixai[anthropic] into ${CLAUDE_PLUGIN_DATA}/venv
├── requirements.txt                 # the pinned engine version the plugin runs
├── example-scorecard.html           # what the --html-out scorecard looks like
└── README.md / TODO.md

../.claude-plugin/marketplace.json   # the repo as its own marketplace

ifixai/plugin/                       # the engine-side backend (ships in the wheel)
├── orchestrator.py                  # `ifixai-diagnose`: run a set → TestRunResult → scorecard
├── usage_profile.py                 # profile JSON → schema-valid fixture + surface tiers
├── claude_code_governance.py        # settings.json/hooks → GovernanceArchitecture adapter
├── fixtures/team_dev.yaml           # usage-derived fixture sized to clear evidence floors
└── recordings/diagnostic_golden.json  # the golden parity anchor (replay default)
```

The engine-side bridge lives with the engine:
[`ifixai/providers/bridge.py`](../ifixai/providers/bridge.py) — `BridgeProvider`
(SUT/judge seams) and the offline transports (`Replay`/`Recording`/`Caching`,
`Constant`/`Stub`/`ModelRoutedJudge`). Live runs no longer go through a bridge
transport: they call the engine's native providers directly (`--mode api`).

## Install (as a Claude Code plugin)

```text
/plugin marketplace add ifixai-ai/iFixAi      # the repo is its own marketplace
/plugin install ifixai@ifixai-ai              # enable it (opt-in: defaultEnabled=false)
# First session provisions the engine into ${CLAUDE_PLUGIN_DATA}/venv, then:
/ifixai:ifixai                                # the operator skill takes over
```

> **Developer-preview install.** The plugin (version `0.1.0`) provisions a separate
> engine package (`ifixai==3.0.2`, pinned in `requirements.txt`) — the two versions
> are independent. Because `3.0.2` is **not on PyPI yet**, the provisioning step
> currently fails; until it's published, point the bootstrap at a local engine by
> setting `IFIXAI_ENGINE_SPEC` (a wheel path, a directory, or `-e /path/to/iFixAi`)
> in your Claude settings `env` before the first session (`hooks/bootstrap.sh`
> reads it). With it set, the engine installs from source instead of the pin.

## Run it (the engine CLI directly)

After provisioning, the skill calls `ifixai-diagnose` from the plugin's managed
venv. From a source checkout you can call it the same way via the repo venv:

```bash
DIAG="${CLAUDE_PLUGIN_DATA}/venv/bin/ifixai-diagnose"   # or .venv/bin/ifixai-diagnose from source

# Offline rehearsal (free, canned/recorded replies, clearly labelled NOT a diagnostic):
"$DIAG" --mode replay

# From a discovery-produced usage profile (the skill's front door): names the
# detected agent, prints the FULL fixture (roles, who-can-use-which-tool, policies)
# and writes it to a visible ifixai-fixture.yaml beside the profile (override with
# --fixture-out) — what you see is what gets graded — plus cost + account-risk;
# runs only after --yes. A live run reads each provider's key from the env
# (here ANTHROPIC_API_KEY); --json-out is the CI source of truth and --artifact-out
# the interactive view:
"$DIAG" --profile /tmp/ifixai-profile.json --mode api \
    --provider anthropic \
    --preset standard --json-out results.json --artifact-out scorecard.html --yes

# Test another provider, graded by an independent / cross-vendor panel of judges
# (each judge's key comes from its own provider env var):
"$DIAG" --profile /tmp/ifixai-profile.json --mode api \
    --provider openai \
    --judge anthropic --judge gemini \
    --preset full --json-out results.json --artifact-out scorecard.html --yes

# Run a hand-edited fixture verbatim (swap --profile for --fixture):
"$DIAG" --fixture /tmp/ifixai-fixture.yaml --mode api \
    --provider anthropic --preset standard --yes

# Diff against a prior run by passing its saved JSON to the artifact:
"$DIAG" --profile /tmp/ifixai-profile.json --mode api --provider anthropic \
    --preset standard --artifact-out scorecard.html --prev-json previous.json --yes

# Grade the deterministic control plane too (adds B02), from a real settings file:
"$DIAG" --mode stub --settings .claude/settings.json
```

## Findings fed back into the plan

- **R14** (high impact): the mandatory-minimum gate failed *closed* on
  `insufficient_evidence`, so B01 alone capped every usage-derived run at D — the
  plan's §6 claim that those inspections are merely "excluded from the grade" was
  wrong for the gate. Fixed in the engine; the corrected behavior is the reason a
  clean run now grades on its behavioral results.
- **Any-model live path**: the live run now uses the engine's native providers
  (`--mode api --provider <p>`), not a Claude-only CLI transport — so the agent
  under test and the judge(s) can be any provider, graded self / independent /
  cross-vendor panel. Keys are env-only (a missing one fails fast naming the
  variable); the `claude -p` headless transport was removed. What remains: broad
  live validation across providers (latency, rate-limits, provider-side refusals).
