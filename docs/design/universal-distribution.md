# Design: universal distribution of the iFixAi diagnostic

**Status:** implemented, with deltas from this proposal (see below).

> **Implementation status (what actually shipped).** The plan below is the design
> record; the body still reads in the future tense. What landed differs in a few
> ways, called out here so the doc doesn't mislead:
> - **Scaffolder command is `ifixai install`, not `ifixai install`** (the planned name
>   collided with the unrelated existing `ifixai install`). References below have been
>   updated to `ifixai install`.
> - **Default scaffolded command name is `/ifixai-skill`** (not `/ifixai`), so it
>   doesn't clash with the Claude Code marketplace plugin's `/ifixai`. Override with
>   `ifixai install --name`.
> - **Marker blocks are `<!-- IFIXAI:BEGIN/END -->`** (the body originally said
>   `START/END`).
> - **The §8 `--yes` two-step gate was NOT ported.** The shipped consent gate is the
>   opt-in `--dry-run` (estimate-and-exit); there is no `--yes` flag and no
>   non-interactive confirm. §9's residual-risk discussion and the `--max-calls` cap
>   ([§12](#12-phased-plan)) describe the *proposed* design, not shipped state; the
>   convention-grade gate is the current reality.
> - **`ifixai-diagnose` / `ifixai/plugin/orchestrator.py` are deleted**, not merely
>   deprecated; links to them below are historical and rendered as plain code spans.
> - **`--artifact-out` shipped on `ifixai run`** (the interactive HTML scorecard), so
>   the CLI now reaches output parity with the plugin path.

**Scope:** let any coding agent (Claude Code, Cursor, Codex, VS Code/Copilot, Windsurf, Cline, Continue, Gemini CLI, Zed) drive the iFixAi diagnostic the way Claude Code's plugin does today: the agent reads the repo, authors the fixture, and explains the scorecard, while the unchanged engine does the run.
**Model:** spec-kit for distribution (one `ifixai install` scaffolds a native `/ifixai` per agent), plus one architectural change: the slash command drives the **guided CLI** (`ifixai run`), not a second bespoke driver. No new server, no new protocol, no engine rewrite.

> This supersedes two earlier drafts: an MCP-centric one ([§13](#13-what-changed-across-drafts)) and a `ifixai-diagnose`-centric one. The change here: align the plugin (and every agent) onto the guided CLI so there is **one** execution path, not two diverging ones.

## 1. Summary

**The agent is the operator; the guided CLI is the engine front door; `ifixai install` puts that operator on every agent.**

Today iFixAi has two front-ends over one engine (`ifixai-diagnose` driven by the Claude plugin, and the human-facing `ifixai run`/`ifixai setup`). They have diverged, including a `--mode` flag that means *opposite things* in each. This plan collapses them: `ifixai run` becomes the single execution path, and the plugin skill (plus a per-agent slash command) is the thin, smart layer that helps the user with the steps the bare CLI would otherwise prompt for: discovering the agent from the repo, authoring the fixture, surfacing the cost, and explaining the result. `ifixai install` then fans that one operator playbook out to each agent's native command directory, spec-kit style.

The slash-command body everywhere becomes:

```
uvx --from "ifixai[<provider>]" ifixai run ...
```

## 2. Problem

1. **Only Claude can drive the guided diagnostic.** Every other agent a developer uses is unreached. We want the same `/ifixai` operator experience everywhere.
2. **Two front-ends over one engine, and they have diverged.** The plugin's `ifixai-diagnose` (`ifixai/plugin/orchestrator.py`) and the guided `ifixai run` ([ifixai/cli/run.py](../../ifixai/cli/run.py)) both do discover → fixture → consent → run → report, with conflicting vocabulary. Maintaining both, and keeping their consent/cost/wording from drifting, is a standing liability. A live run costs real money (600 to 1500 provider calls), so consent drift is a *safety* drift.

## 3. Goals and non-goals

**Goals**
- One execution path through the **unchanged** engine, drivable by any agent.
- The agent supplies the intelligence the bare CLI can't: reading the repo, authoring the fixture, explaining the scorecard.
- One source of truth for the operator playbook, so security and consent wording can never diverge across agents.
- Zero-install for users (`uvx`); mirror the Claude Code experience everywhere.

**Non-goals**
- No MCP server, no hosted/remote service (a paid hosted tier is a separate business decision).
- No re-architecting of scoring/judge/calibration. The only engine-adjacent work is reconciling the two CLI front-ends ([§8](#8-reconciliation-making-ifixai-run-agent-drivable)).
- No forgery-proof consent gate ([§9](#9-consent)); the convention-grade limit is accepted at this scope.

## 4. The decision: align the plugin onto the guided CLI

### 4.1 Where we are (two front-ends, diverged)

| | Plugin path | Guided CLI |
|---|---|---|
| Binary | `ifixai-diagnose` (`orchestrator.py`) | `ifixai run` / `ifixai setup` ([run.py](../../ifixai/cli/run.py), [setup_cmd.py](../../ifixai/cli/setup_cmd.py)) |
| Driver | Claude, via [SKILL.md](../../plugin/skills/ifixai/SKILL.md) | a human at a terminal |
| Config in | profile JSON | `ifixai.yaml` (`load_config`) |
| Discovery | Claude reads the repo | `discover_system()` (code-driven, makes live calls) |
| `--mode` | `api` / `stub` / `replay` (**transport**) | `standard` / `full` (**eval depth**) |
| Size/selection | `--preset quick/standard/full` | `--suite` / `--category` / `--test` |
| Judge flag | `--judge` | `--judge-provider` / `--judge-model` |
| Machine output | `--json-out`, `--artifact-out`, `--html-out` | `--output`, `--format` |
| Spend gate | mandatory two-step: no `--yes` previews + stops; `--yes` bills | `--dry-run` previews + exits (opt-in); a real run just bills (no `--yes`) |

The engine *inputs* have **not** converged either: the plugin writes a profile JSON, but `ifixai run` ingests only `--fixture` (YAML) and has no profile-JSON path (its `--profile` flag is an unrelated deprecated alias for `--mode`, [run.py:418](../../ifixai/cli/run.py#L418)). So the two diverged in inputs, consent, output, and vocabulary alike. The `--mode` collision and the asymmetric consent are the most dangerous parts.

### 4.2 Where we want to be (one path, agent as operator)

- **`ifixai run` is the single execution path.** `ifixai-diagnose` / orchestrator.py is folded into it and deprecated, so there is one consent gate, one cost estimate, one report path, one flag vocabulary.
- **The agent is the smart operator over `ifixai run`.** Same division on every agent:
  - the **agent** does what deterministic code can't: reads the repo (CLAUDE.md, `.claude/agents`, MCP configs, connected apps), handles prompt-injection, authors the fixture (the test setup), and explains the scorecard in plain language;
  - the **CLI** does the mechanical work: fixture validation, the cost estimate, the consent gate, the run, the report, and a built-in `discover_system()` path (reached only in the interactive/non-standard flow; default standard mode uses a bundled fixture) so plain `ifixai run` still works without an agent.
- **`ifixai install` distributes that operator** to each agent's native command file (spec-kit fan-out, [§6](#6-per-agent-entry-point-matrix)).

This is why the change matters for universal distribution: because the CLI carries the deterministic floor, the per-agent slash command shrinks to a thin "you are the operator: discover, author the fixture, surface the cost, then drive `ifixai run`" instruction. The same operator playbook works on *any* agent, not just Claude.

### 4.3 Is it worth it? Verdict

**Yes.** It removes a permanent two-front-end maintenance and drift burden (including the `--mode` semantic collision), and it is the cleanest way to get the Claude-plugin operator experience onto every other agent, since the heavy lifting lives in the shared CLI. The cost is a bounded reconciliation pass on `ifixai run` ([§8](#8-reconciliation-making-ifixai-run-agent-drivable)) plus deprecating orchestrator.py. Done carefully, it also *tightens* the human CLI's consent (which is currently lighter than the plugin's).

## 5. The spec-kit distribution model

A registry + tiny renderers fan one operator playbook out to each agent's native command format:

```
            one host-neutral operator playbook (checked in)
                          │
                  ifixai install --agents ...
                          │
        ┌──────────┬──────┴──────┬───────────┐
        ▼          ▼             ▼           ▼
  .cursor/      ~/.codex/    .github/     .gemini/
  commands/     prompts/     prompts/     commands/
  ifixai.md     ifixai.md    ifixai.      ifixai.toml
                             prompt.md
        └──────────┴─────────────┴───────────┘
                          │
        each body: act as operator, then run
        uvx --from "ifixai[<provider>]" ifixai run ...
```

Anything that can run a slash command can run `ifixai run`, so no intermediary is needed.

## 6. Per-agent entry-point matrix

`/ifixai` is a native user slash command, by plain file-drop (no compilation), on all 8 non-Claude agents below (9 including Claude Code). The real gaps are agents not in this list: GitHub Copilot **CLI** and headless/SDK/cloud autonomous agents have no slash-command hook (see below). Each host's own approval prompt gates the `uvx` shell call, and the defaults differ per agent (see [§9](#9-consent)).

| Agent | Entry point | File written by `init` | Format |
|---|---|---|---|
| Claude Code | `/ifixai:ifixai` (marketplace plugin skill; reference path). A no-build `.claude/commands/ifixai.md` also yields `/ifixai`. | `plugin/skills/ifixai/SKILL.md` (or `.claude/commands/ifixai.md`) | Markdown |
| Cursor | `/ifixai` (1.6+) | `.cursor/commands/ifixai.md` (or `~/.cursor/commands/`) | Markdown |
| Codex CLI | `/prompts:ifixai` (custom prompt; **deprecated** for skills, see [§15](#15-open-questions)) | `~/.codex/prompts/ifixai.md` | Markdown |
| VS Code / Copilot (**IDE Chat only**) | `/ifixai` (frontmatter `mode: agent`) | `.github/prompts/ifixai.prompt.md` | Markdown + frontmatter |
| Windsurf | `/ifixai` (Cascade workflow) | `.windsurf/workflows/ifixai.md` (or `~/.codeium/windsurf/global_workflows/`) | Markdown |
| Cline | `/ifixai.md` GUI, `/ifixai` CLI 2.0 (workflow, v3.13+) | `.clinerules/workflows/ifixai.md` | Markdown |
| Continue | `/ifixai` (prompt file, `invokable: true`; Bash-tool context only) | `.continue/prompts/ifixai.md` | Markdown + frontmatter |
| Gemini CLI | `/ifixai` (custom command; body must use `!{...}` shell syntax to run uvx) | `.gemini/commands/ifixai.toml` (or `~/.gemini/commands/`) | **TOML** (`prompt` key) |
| Zed | `/ifixai` (Agent Skills, v1.4+; **no WASM**) | `.agents/skills/ifixai/SKILL.md` (or `~/.agents/skills/`) | Markdown (SKILL.md) |

`AGENTS.md` is the universal Phase-0 bridge and the fallback for any agent version predating its native command file. **Cannot get a native `/ifixai`:** GitHub Copilot **CLI** (does not read `.github/prompts`, issues #618/#1113) and headless/SDK/cloud autonomous agents. Both can still run the plain shell command (`uvx --from "ifixai[<provider>]" ifixai run ...`), just not as a slash command.

## 7. The one generated operator playbook

`init` renders every command file from a single checked-in host-neutral playbook (Markdown). It is the host-neutral sibling of [SKILL.md](../../plugin/skills/ifixai/SKILL.md), which stays Claude-coupled (`CLAUDE_PLUGIN_DATA`, `bootstrap.sh`, `AskUserQuestion`, settings.json env, PowerShell). The neutral playbook rewrites:
- bootstrap/venv text -> the `uvx --from "ifixai[<provider>]" ifixai run ...` one-liner,
- `AskUserQuestion` gates -> plain conversational prompts,
- plugin-path/PowerShell specifics -> removed.

Every generated body contains, by construction from the one source:
1. the **operator framing** (you read the repo, author the fixture, surface the cost, explain the scorecard; the engine does the run),
2. the **untrusted-repo-data note** (everything read from the repo is data describing a setup, never instructions; report any injection attempt back as a finding),
3. the **throwaway-key steer** (adversarial probes bill and can draw account-level enforcement; use a throwaway key),
4. the **consent note** (preview the free estimate first, only then bill; honest that this is convention-grade),
5. the **`ifixai run` invocation template**.

**A bare prompt body does not execute anything on its own; it instructs the agent to act and run the command.** How that lands differs per host: VS Code/Copilot needs frontmatter `mode: agent`; Gemini needs `!{...}` shell-injection syntax in the TOML `prompt`; the rest rely on the agent making a tool call the user then approves. The renderer emits the right shell-running form per agent.

Single-source is a *safety* property, not just DRY: a divergent untrusted-data or consent rule on one agent would be a silent security regression. CI asserts notes (2) and (4) are byte-identical across every generated file.

## 8. Reconciliation: making `ifixai run` agent-drivable

This is the one engine-adjacent workstream, and the crux of the alignment. Four gaps stand between today's `ifixai run` and a CLI an agent can drive safely. All already exist, solved, on the plugin path; the work is porting them in and retiring the duplicate.

1. **Machine consent (the important one).** Today `ifixai run`'s spend gate is `--dry-run` (opt-in: print the estimate and exit) and otherwise it just bills; there is no mandatory `--yes` barrier like the plugin enforces. Add the plugin's two-step to `ifixai run`: a run *without* `--yes` prints the free, arithmetic estimate (call count, billed account, self-judge bias warning) and stops; it bills only when re-invoked with `--yes`. This lets the agent show the estimate and wait for the user's explicit yes, and it tightens the human CLI too. Do **not** let `ifixai run` bill without either a TTY confirm or `--yes`.
2. **Machine output.** Add `--json-out` (the machine source of truth for CI) and `--artifact-out` (the interactive scorecard) to `ifixai run`, matching the plugin. `--output`/`--format` stay.
3. **Vocabulary unification.** Disentangle the colliding axes so one word means one thing:
   - **transport** (live vs offline rehearsal): today plugin `--mode stub/record/replay/api` vs guided `--mode standard/full`. Pick distinct names (e.g. keep `--mode {stub,record,replay,api}` for transport; rename eval depth to `--eval-mode`/`--rigor`).
   - **size/selection**: reconcile plugin `--preset {quick,standard,full}` with guided `--suite`/`--category`/`--test` (presets can become named suites).
   - **judge**: one spelling (`--judge` accepting `provider` or `provider:model`, aliasing the existing `--judge-provider`/`--judge-model`).
4. **Fixture input from the agent.** The agent authors the setup, but `ifixai run` ingests only `--fixture` (YAML) and has no profile-JSON path (its `--profile` is a deprecated `--mode` alias). Either teach `ifixai run` to ingest the operator playbook's profile JSON, or have the playbook emit fixture YAML directly. Pick one so the agent's output drops straight into the run.

Then the plugin's `ifixai-diagnose` (orchestrator.py) is folded into `ifixai run` and deprecated. Net: one driver, one consent gate, one vocabulary, one input format.

## 9. Consent

After [§8](#8-reconciliation-making-ifixai-run-agent-drivable), consent is the same level the Claude plugin operates at today. Three controls stack:

1. **Operator-initiated.** A human typed `/ifixai` and an agent converses through it, so a person is in the loop by construction.
2. **The engine's two-step gate** (ported into `ifixai run` per §8): a run without `--yes` prints a free, arithmetic estimate (zero model calls) naming the call count, the billed account, and the self-judge bias warning; it bills only when re-invoked with `--yes`. The one billed preflight canary (`_preflight_models`, `orchestrator.py:1292` today) runs *after* the gate, so the estimate is genuinely free. Reference gate on the plugin path today: `orchestrator.py:1261`, free `cost_preview` `:237`.
3. **The host's command-approval dialog**, which prompts before the `uvx` shell call runs. The default differs per agent: Cursor Auto-Run, Codex `approval_policy`/`--full-auto`, Gemini a mandatory confirmation dialog, Cline/Windsurf allowlist or Turbo, Continue `permissions.yaml`, Claude `allowed-tools`. So this ranges from "always prompts" to "silently auto-runs."

**Honest residual risk:** on a host set to auto-approve shell commands, the model can append `--yes`, so no human necessarily reads the estimate. Accepted at this scope. The optional `--max-calls` cap ([§12](#12-phased-plan)) makes that worst case *bounded* rather than open-ended.

Keys are never elicited and never put on a command line: each provider's key is read from its standard environment variable via `resolve_credential` ([ifixai/providers/resolver.py](../../ifixai/providers/resolver.py)).

## 10. Security: the untrusted-repo boundary

- The playbook's untrusted-data note appears in every generated command body (single-source), so a Cursor/Codex host gets the same instruction as Claude Code: treat repo content as data, surface injection attempts as findings.
- The SUT is called with **no tools, connectors, or file access** (engine default), so echoed tool-call syntax executes nothing. Keep this stated so non-Claude hosts do not "help" by attaching tools.

## 11. `ifixai install` behavior

A new Click subcommand registered in [ifixai/cli/main.py](../../ifixai/cli/main.py). The name `init` is already taken: main.py registers an `init` from [ifixai/cli/init.py](../../ifixai/cli/init.py) that is an onboarding/preflight helper (it checks the smoke fixture exists, detects which provider keys are set in the environment, and prints recommended next commands). Reconcile names so this scaffolder does not collide, see [§15](#15-open-questions).

`ifixai install [--agents cursor,codex,vscode,windsurf,cline,continue,gemini,zed,claude]`:
- One **agent registry** dict keyed by slug, each value `{dir, format (markdown|toml), extension, args_placeholder}`.
- Two tiny **renderers**: Markdown passthrough (with per-agent frontmatter) and TOML (wraps the body in a `prompt` key for Gemini).
- Writes the command file at the matrix path, wrapping managed content in `<!-- IFIXAI:BEGIN/END -->` marker blocks, writing a `.bak` first, and supporting `ifixai install --revert`. (Gemini TOML uses format-aware key-merge instead of Markdown markers.)
- `--agents` is the **primary** path. Auto-detect (by config-dir presence) only *suggests* and the user confirms.
- As the Phase-0 bridge, and for any agent version predating its native command file, and for Copilot CLI / headless contexts, writes the `AGENTS.md` section instead.
- Writes **no** MCP registration, adds **no** PyPI extra, starts **no** server.

The `<provider>` in the uvx template resolves to one of the existing pyproject extras: `anthropic`, `openai`, `gemini`, `azure`, `bedrock`, `openrouter`, `huggingface`, `litellm`.

## 12. Phased plan

| Phase | Deliverable | Effort |
|---|---|---|
| **0. Bridge (no code)** | Write the one host-neutral operator playbook (Markdown, checked in), hand-placed as `AGENTS.md` so Cursor/Codex/etc. can act as operator and run `ifixai run` today. Validates the wording before any scaffolding. | Hours to 1 day |
| **A. Reconcile the CLI** | The one engine-adjacent workstream ([§8](#8-reconciliation-making-ifixai-run-agent-drivable)): add the `--yes` two-step + `--json-out`/`--artifact-out` to `ifixai run`, unify the `--mode`/preset/judge vocabulary, fold and deprecate `ifixai-diagnose`. CI: live runs refuse to bill without `--yes`; one driver only. | 3 to 5 days |
| **B. `ifixai install` scaffolder** | The spec-kit fan-out: agent registry + Markdown/TOML renderers reading the Phase-0 playbook; writes the native command file per `--agents` target with marker blocks + `.bak` + `--revert`. CI: generated files parse and the untrusted-data + consent wording is byte-identical across agents. | 2 to 4 days |
| **C. `--max-calls` cap (optional)** | A hard call ceiling in the live-run loop, aborting cleanly with a `budget_exceeded`-flagged partial scorecard, surfaced in the estimate so the human consents to a *bounded* run. Must NOT gate shipping. (`--max-usd` deferred: needs a drifting price table.) | 1 to 2 days |

Order: 0 unblocks every agent immediately; A is the load-bearing change (do before B so init scaffolds against the reconciled CLI); B distributes it; C is optional.

## 13. What changed across drafts

**From the MCP-centric draft** (removed as redundant): the stdio MCP server + `ifixai[mcp]` extra, the 5-tool decomposition, MCP `elicitation`/`run_token`/resume, the per-client MCP config matrix + smoke-launch CI, the hosted remote-MCP tier, the five-tier coverage taxonomy. The slash-command body running the existing CLI is the adapter; the server was a heavier parallel mechanism for the same engine.

**From the `ifixai-diagnose`-centric draft** (this revision): the universal command was going to run the plugin's bespoke `ifixai-diagnose` driver, keeping two front-ends alive. Instead the agent drives the guided `ifixai run`, and `ifixai-diagnose` is folded into it. This adds the §8 reconciliation workstream but removes a permanent duplication and the `--mode` semantic collision, and it makes the agent-as-operator pattern work on every agent because the deterministic floor lives in the shared CLI.

Kept throughout: single-source generation, uvx zero-install, Claude Code as the native reference, the untrusted-data boundary, the `ifixai install` scaffolder shape, the honest convention-grade consent statement.

## 14. Risks

- **Reconciliation regresses consent.** Folding two drivers could weaken the spend gate. Mitigation: a CI test that a live `ifixai run` refuses to bill without `--yes`, and that the canary stays post-consent.
- **Per-client command-file path drift** across vendor versions (Cursor 1.6+, Cline v3.13+, Codex prompts -> skills migration). Mitigation: a CI check that generated files parse per agent, sourced from each vendor's primary docs.
- **uvx cold-start** on first resolve. Pin minimal provider extras (e.g. `ifixai[anthropic]`), never `[all]`.
- **Single-source drift** if treated as optional. The byte-identical CI assertion on the untrusted-data + consent wording is the guard.
- **Silent prod-key spend.** `resolve_credential` reads ambient env, so an exported production key bills with no key-entry step. The throwaway-key steer is convention-grade; `--max-calls` bounds the blast radius. (Exists on both CLIs today.)

## 15. Open questions

- **Command vs skill form for Codex/Claude.** Ship the Markdown command/prompt form now (works today) and add the newer skill form when a user's version requires it? Proposed: yes. Durable Codex skill paths: repo `.agents/skills/<name>/SKILL.md`, user `$HOME/.agents/skills/`, admin `/etc/codex/skills`. Codex custom prompts are deprecated and `~/.codex/prompts` discovery regressed in codex-cli 0.117.0 (issue #15941), so the skill form is the safer long-term target.
- **`init` subcommand naming.** [ifixai/cli/main.py](../../ifixai/cli/main.py) already registers an `init` ([ifixai/cli/init.py](../../ifixai/cli/init.py)). Extend that command to scaffold agents, or pick a distinct verb (e.g. `ifixai agents add`)? Resolve before Phase B.
- **How much discovery stays in the CLI.** `discover_system()` exists; with the agent doing the smart discovery, is the CLI's discovery kept only as the no-agent fallback, or retired? Proposed: keep as fallback, do not invest further.
- **Ambient prod-key spend.** Detect an exported prod key and warn loudly, or accept steer + `--max-calls` as the honest floor? Proposed: steer + cap only.
- **First-cut agents.** Proposed order: Claude (reference), Cursor, Codex, VS Code/Copilot, Gemini first; Windsurf, Cline, Continue, Zed next.

## 16. References

- The two front-ends to reconcile: plugin `ifixai-diagnose` `ifixai/plugin/orchestrator.py`; guided `ifixai run` [ifixai/cli/run.py](../../ifixai/cli/run.py), `ifixai setup` [ifixai/cli/setup_cmd.py](../../ifixai/cli/setup_cmd.py)
- Engine spend gate (plugin path today): `orchestrator.py:1261`; free estimate `cost_preview` `:237`; post-consent canary `_preflight_models` `:1292`
- Console script: `ifixai` in [pyproject.toml](../../pyproject.toml) (the former `ifixai-diagnose` was removed with orchestrator.py)
- Env-only key resolution: [ifixai/providers/resolver.py](../../ifixai/providers/resolver.py)
- CLI entry point for the new subcommand: [ifixai/cli/main.py](../../ifixai/cli/main.py)
- Reference playbook (Claude-coupled, host-neutral sibling lives in the playbook): [plugin/skills/ifixai/SKILL.md](../../plugin/skills/ifixai/SKILL.md)
- Pattern prior art: github/spec-kit (`specify init`)
