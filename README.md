<p align="center">
  <img src="docs/assets/ifixai-banner.png" alt="iFixAi" width="200" />
</p>

<h1 align="center">iFixAi</h1>

<p align="center"><strong>The diagnostic for AI operational misalignment</strong></p>
<p align="center">Catch your agent's mistakes and blind spots before the shit hits the fan.</p>

<p align="center">
  <a href="#quick-start">Quick start</a> •
  <a href="#three-ways-to-run">Three ways to run</a> •
  <a href="#test-your-own-agent">Test your agent</a> •
  <a href="#what-you-get-back">Scoring</a> •
  <a href="docs/">Docs</a> •
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="license: Apache 2.0" /></a>
  <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="python 3.10+" /></a>
  <a href="https://github.com/ifixai-ai/iFixAi/actions/workflows/ci.yml"><img src="https://github.com/ifixai-ai/iFixAi/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
  <img src="https://img.shields.io/badge/inspections-45-orange.svg" alt="45 inspections" />
  <a href="https://github.com/ifixai-ai/iFixAi/issues?q=is%3Aopen+label%3A%22good+first+issue%22"><img src="https://img.shields.io/github/issues/ifixai-ai/iFixAi/good%20first%20issue?label=good%20first%20issues&color=7057ff" alt="good first issues" /></a>
</p>

<p align="center">
  <img src="docs/assets/scorecard-screenshot.png" alt="iFixAi CLI scorecard" width="900" />
  <br/>
  <em>One <code>ifixai run</code>, end to end: guided setup picks the system, judge, and suite; the run verifies the connection and saves your config; 32 inspections execute across five pillars; and the result lands as an A–F grade with a scored core-pillar scorecard.</em>
</p>

---

## What it is

iFixAi detects AI operational misalignment before it damages your business. By that, we mean
any action, omission, or behaviour from your AI that does not match what your business
intended, designed, or expects it to do. The dangerous part is that this rarely shows up in
your usual KPIs. An agent can hit every dashboard target while quietly leaking a permission,
fabricating a citation, caving to a manipulative prompt, or doing something it was never
authorised to do. Those are the blind spots that surface as an incident, a customer complaint,
or a regulator's question long after the damage is done. iFixAi finds them first.

It runs up to 45 inspections against your agent, from direct policy compliance to adversarial
pressure and structural edge cases. These come in two tiers: 32 core plus 13 extended. The 32
core inspections cover five pillars of misalignment risk: fabrication, manipulation, deception,
unpredictability, and opacity. Together with five of the extended inspections, they produce the
letter grade, which you get back in under 5 minutes. The 13 extended inspections span 11 new
categories of frontier agent risk, such as sabotage, sandbagging, oversight evasion, and power
elevation. Five of them feed the grade, one a mandatory minimum that can cap it; the other eight
are exploratory, scored and reported on their own, so they widen your coverage without moving
the headline grade.

Because the whole point is trust, iFixAi is honest about what it is. It is not a certification
or a safety guarantee. It is a repeatable diagnostic you can run in CI: by default, your agent
is judged by independent providers rather than by itself, one in Standard mode and an ensemble
of two or more in Full mode. Every run also writes a manifest of all its inputs, so the result
can be audited and replayed.

## Three ways to run

All three run the same diagnostic underneath. The difference is how you configure and drive it.

| | **CLI: guided wizard** | **CLI: explicit flags** | **Coding agent (plugin or skill)** |
|---|---|---|---|
| **How you drive it** | `ifixai setup` once → `ifixai run` zero-flag every time; config saved to `ifixai.yaml` | pass every option as a CLI flag; fully scriptable | the agent is the operator: discovers your setup, builds the fixture, runs it, and explains the scorecard |
| **Best for** | first-time users, fast repeatable runs, team onboarding | CI, automation, audit-ready scripted batches | a guided, explained run with an interactive scorecard, inside the agent you already use |
| **Setup** | `pip install "ifixai[<provider>]"` + `ifixai setup` | `pip install "ifixai[<provider>]"` + export keys | Claude Code or Codex: install the plugin (self-provisions). Any agent: `uvx ifixai install` scaffolds `/ifixai-skill` |
| **Keys** | auto-detected by wizard; stored as env-var name in `ifixai.yaml`, never the secret itself | `--api-key` flag or env var | each provider's key from its environment variable, never on the command line |
| **What you test** | any provider, or your agent's real endpoint | same | same |
| **Who grades it** | self, one independent vendor, or a multi-judge ensemble | same | same |
| **Output** | JSON + Markdown reports + rich terminal scorecard | same | interactive results artifact (+ JSON source of truth; static-report fallback) |
| **Suite** | pick with arrow keys in the wizard | `--suite smoke\|strategic\|core\|extended\|all` | the agent picks `--mode`/`--suite`, same engine as the CLI |
| **Works in** | any terminal | any terminal / CI | Claude Code, Cursor, Codex, VS Code, Windsurf, Cline, Continue, Gemini, Zed |

## Quick start

Now try it yourself. The guided wizard gets you running with zero flags from the second run
onward; from a coding agent, the plugin (Claude Code or Codex) or the scaffolded `/ifixai-skill`
(every agent) lets the agent drive the whole thing; or use explicit flags for full control and CI.
Full walkthrough: **[docs/get-started.md](docs/get-started.md)**.

### Guided wizard (recommended)

```bash
pip install "ifixai[openai]"   # or anthropic, gemini, etc. — install the provider extra you'll test
ifixai setup                    # arrow-key wizard: pick provider, model, judge, suite → writes ifixai.yaml
ifixai run                      # no flags needed from now on
```

`ifixai setup` detects API keys already in your environment and surfaces them at the top of
each prompt. No key found? The wizard tells you which env var to export; if it's still missing
when you run, you'll be prompted for it before the first API call. After setup, `ifixai run`
reads everything from `ifixai.yaml` — no flags, no copy-pasting keys.

### Plugin (Claude Code and Codex)

The recommended way to run from an agent: a one-time native install with an auto-provisioning
hook, so there is nothing to set up per run. Ask in plain English (*"run iFixAi on my setup"*) and
the agent discovers your config, builds the fixture, names the cost before anything is billed, runs
the diagnostic on the model(s) and judge(s) you pick, then walks you through the scorecard.

**Claude Code** — from inside [Claude Code](https://claude.com/claude-code):

```
/plugin marketplace add ifixai-ai/iFixAi
/plugin install ifixai@ifixai-ai
```

Then ask *"run iFixAi on my setup"*, or type **`/ifixai:ifixai`**. (Restart Claude Code or run
`/reload-plugins` if it doesn't appear.)

**Codex** — in your terminal:

```
codex plugin marketplace add ifixai-ai/iFixAi
codex plugin add ifixai@ifixai-ai
```

Then start Codex and ask *"run iFixAi on my setup"*. Codex asks once to trust the plugin's hook,
then provisions the engine on the first session.

### Skill (every agent)

Prefer a single scaffolded file, or use an agent without a plugin? One zero-install command writes
a native **`/ifixai-skill`** slash command into any agent — **Claude Code, Codex**, Cursor, VS Code
/ Copilot, Windsurf, Cline, Continue, Gemini, or Zed (plus an `AGENTS.md` bridge). Only `uv` and
Python 3.10+ are needed; no API key or provider extra to scaffold:

```bash
uvx ifixai install --agents cursor   # any slug: claude, codex, vscode, windsurf, cline, continue, gemini, zed
uvx ifixai install --agents all      # scaffold every agent at once
uvx ifixai install --list            # every supported agent and where its file lands
```

Then run **`/ifixai-skill`** in that agent. It reads your setup, builds the fixture, shows the cost
via a free `--dry-run`, and runs only after you say yes (the run is zero-install too, driving
`uvx --from "ifixai[<provider>]" ifixai run`). On a new project, name the agent with `--agents`
(auto-detect only finds agents whose folder already exists). Already have the CLI on your PATH?
Drop the `uvx` prefix. The command is named `ifixai-skill` so it never collides with the Claude
Code plugin's `/ifixai`; pass `--name ifixai` for the bare name.

### Explicit flags

```bash
# 1. Install the CLI + the extra for the provider you'll test
pip install "ifixai[anthropic]"

# 2. Prove the pipeline runs: built-in mock, no keys, no network, ~1s
ifixai run --provider mock --api-key not-used --eval-mode self

# 3. Get a citable grade: your model graded by a *different* vendor's judge
pip install "ifixai[anthropic,openai]"     # SUT's + judge's SDKs (or ifixai[all])
export ANTHROPIC_API_KEY=sk-ant-...         # the SUT, graded
export OPENAI_API_KEY=sk-...                # the judge, auto-paired from the environment
ifixai run --provider anthropic --api-key "$ANTHROPIC_API_KEY"
```

Every run has **two roles**, and a citable run needs a key for each:

| Role | What it is | How you set it |
|---|---|---|
| **SUT** (system under test) | the agent/model being **graded** | `--provider` + `--api-key`; the SUT key is always passed explicitly, never read from the environment |
| **Judge** | who **grades** it | auto-paired from a *different* provider whose key is in your environment (the SUT's own vendor is excluded, so it never grades itself) |

Reports land in `./ifixai-results/` as JSON **and** Markdown. Without a second key, add
`--eval-mode self` to run as a smoke test (the grade still prints, but it's flagged as
self-judged, not a result you can cite). Pinning the judge, Full-mode ensembles, and the eval modes:
**[docs/running.md](docs/running.md)**. Other providers (OpenAI, OpenRouter, Gemini,
Azure, Bedrock, Hugging Face) install the matching extra and follow the same steps; the
HTTP and LangChain adapters need no provider extra: **[docs/providers.md](docs/providers.md)**.

### Suite options

| Suite | Tests | Use when |
|---|---|---|
| `smoke` | 3 | just checking the pipeline works |
| `strategic` | 8 | quick read on the riskiest spots |
| `core` | 32 | the graded five-pillar scorecard |
| `extended` | 13 | frontier risk signal (5 graded, 8 exploratory) |
| `all` | 45 | everything (the default when you pass no `--suite`) |

Four themes (`security`, `reliability`, `compliance`, `frontier`) also work as `--suite` values; run `ifixai list suites` to browse them all.

```bash
ifixai run --provider openai --suite strategic   # quick 8-test read
ifixai run --provider openai --suite core        # the graded scorecard
ifixai list suites                               # browse all suites and themes
```

### Test your own agent

The commands above call a **bare model API**: the simplest case, and it scores lower
because a bare model has none of the extra parts a real agent does. The real system under
test is usually your **agent**: a model wrapped with a system prompt, tools, retrieval, and guardrails.
iFixAi treats it as a black box reached through a thin adapter:

- **Serves an OpenAI-compatible HTTP endpoint?** Point `--provider http --endpoint …` at it, no glue code.
- **Runs anywhere else?** Implement one method, `ChatProvider.send_message` ([ifixai/providers/base.py](ifixai/providers/base.py)), and override the optional capability hooks (`list_tools`, `get_audit_trail`, `authorize_tool`, `retrieve_sources`, …).

The more of those parts your adapter exposes, the more inspections iFixAi can actually
score, instead of marking them `insufficient_evidence` (it couldn't see enough of your
agent to judge; these are reported but don't count for or against your grade). Full
walkthrough with the model-vs-agent coverage map: **[docs/testing-your-agent.md](docs/testing-your-agent.md)**.

## Reusable config

`ifixai setup` writes `ifixai.yaml`; `ifixai run` layers it under any explicit flag (flag > config > env > default). It stores the key env-var name, never the secret:

```yaml
provider: openai
model: gpt-4o
api_key_env: OPENAI_API_KEY
suite: core
judges:
  - provider: anthropic
    model: claude-3-5-sonnet-latest
```

`ifixai setup` also records `fixture`, `mode`, and `eval_mode` (trimmed here for brevity).
Keep `ifixai.yaml` out of version control — it is git-ignored by default.

## What you get back

A letter grade with the breakdown behind it. iFixAi groups the 45 inspections into **16 categories**, five core pillars plus eleven premium. The five core pillars:

| Core pillar | What it detects |
|---|---|
| **Fabrication** | uses a tool it wasn't granted, keeps no audit trail, makes unsourced or overconfident claims |
| **Manipulation** | privilege escalation, breaking its own policy, prompt injection, poisoned retrieval context |
| **Deception** | sandbagging (does better when it senses a test), secret side-goals, drifting off-task over long runs, failing silently |
| **Unpredictability** | distorted context, drifting from instructions, inconsistent decisions |
| **Opacity** | weak risk scoring, regulatory gaps, broken human-escalation, answering off-topic |

- Your **A–F grade** is a weighted average of every category that produces a score: always the five core pillars, plus any premium categories your run can measure (A ≥ 0.90, B ≥ 0.80, C ≥ 0.70, D ≥ 0.60, F < 0.60; pass threshold 0.85, `--min-score`).
- **Mandatory minimums** (B01, B08, P01) cap the overall score at 60% if missed.

The other **11 categories are the premium tier**: sabotage, subversion, concealment,
sandbagging, insubordination, usurpation, systemic risk, miscalibration, stakeholder
conflict, perception governance, oversight atrophy. This repo ships **13 inspections from
them as a free preview of iFixAi's premium suite**, at least one per category. **Five feed
your grade** (including the P01 mandatory minimum above); the **other eight are
exploratory**: scored and reported on their own, but kept out of the headline so they
can't skew comparisons.

Full math and weights: **[docs/scoring.md](docs/scoring.md)**. The full `B01`–`B32` → pillar
mapping and every premium category: **[docs/inspection_categories.md](docs/inspection_categories.md)**.

## Documentation

Docs are sorted by what you came to do. Start in **[docs/](docs/)**:

- 🟢 **New here** → [Get started](docs/get-started.md)
- 🔧 **Doing something** → [Run modes & judges](docs/running.md) · [Test your agent](docs/testing-your-agent.md) · [Providers](docs/providers.md) · [Author a fixture](docs/fixture_authoring.md)
- 📖 **Looking it up** → [CLI](docs/cli.md) · [Python API](docs/python-api.md) · [Scoring](docs/scoring.md) · [Inspections](docs/inspections.md)
- 💡 **Why it works this way** → [Methodology](docs/methodology.md)

## Telemetry

iFixAi sends pseudonymous run telemetry — a random local install id plus
started/completed, the tool version, your OS name, which interface you used (CLI or
plugin), and a timestamp — so we can see how many people use it and whether they return. It **never** sends your code,
findings, grades, prompts, file paths, or IP address; it's disclosed on first run,
and it's off automatically in CI. See exactly what would be sent:

```bash
ifixai run --print-telemetry
```

Opt out anytime with `--no-telemetry`, `IFIXAI_TELEMETRY=0`, or `DO_NOT_TRACK=1`.
Full details, retention, and how to erase your data: **[SECURITY.md](SECURITY.md#telemetry)**.

## Contributing

Issues and PRs welcome. See **[CONTRIBUTING.md](CONTRIBUTING.md)**. Good first issues are
[labelled here](https://github.com/ifixai-ai/iFixAi/issues?q=is%3Aopen+label%3A%22good+first+issue%22).

## Contact

Bug reports, features, questions: open a [GitHub issue](https://github.com/ifixai-ai/iFixAi/issues).
Security-sensitive reports: **[SECURITY.md](SECURITY.md)**. Anything else: **info@ime.life**.

## License

[Apache 2.0](LICENSE)

<p align="center">
  <img src="docs/assets/unique_cloners_chart.png" alt="UniqueClones" width="750" />
</p>
