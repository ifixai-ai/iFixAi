<p align="center">
  <img src="docs/assets/ifixai-banner.png" alt="iFixAi" width="200" />
</p>

<h1 align="center">iFixAi</h1>

<p align="center"><strong>The diagnostic for AI operational misalignment</strong></p>
<p align="center">Catch your agent's mistakes and blind spots before the shit hits the fan.</p>

<p align="center">
  <a href="#quick-start">Quick start</a> •
  <a href="#two-ways-to-run-it">Two ways to run</a> •
  <a href="#test-your-own-agent">Test your agent</a> •
  <a href="#what-you-get-back">Scoring</a> •
  <a href="#in-the-wild">In the wild</a> •
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
  <img src="docs/assets/ifixai-demo.gif" alt="iFixAi demo" width="720" />
  <br/>
  <em>Recorded from a custom client build — the open-source CLI runs the same diagnostic with different presentation.</em>
</p>

---

## What it is

**Operational misalignment** is any action, omission, or behaviour from your AI that
doesn't match what your business intended — leaking a permission, fabricating a citation,
caving to a manipulative prompt, taking an action it was never authorised to do. It rarely
shows up in your KPIs: an agent can hit every dashboard target while quietly doing all of
the above, and you only find out from an incident, a complaint, or a regulator's question.

iFixAi finds it first. You point it at your agent or model, and it runs up to **45 inspections** —
individual test cases, each probing one way an agent can fail. A second AI from a different
vendor (the **judge**) grades the responses, and you get
back a letter grade (A–F), per-pillar scores, and a reproducible report — in under five
minutes. Because the judge is a different vendor than the thing being graded, the result is
citable, not a model marking its own homework.

It's honest about what it is: **a repeatable diagnostic you can run in your automated test
pipeline (CI) — not a certification or a safety guarantee.**

## Two ways to run it

Same engine, two ways in — the CLI or the Claude Code plugin. **Both test any model,
graded by the judge(s) of your choice** — pick by whether you want Claude to *guide* the
run or to *script* it yourself.

| | **CLI** (`pip install`) | **Claude Code plugin** |
|---|---|---|
| **How you drive it** | you write the **fixture** (the config describing your agent) and CLI flags — scriptable, CI-friendly | Claude is the operator — it discovers your setup, builds the fixture, runs it, and explains the scorecard |
| **What you can test** | any provider, or your agent's real endpoint | any provider (Anthropic, OpenAI, Gemini, Azure, Bedrock, …) — Claude only guides |
| **Who grades it** | any judge: self, one independent vendor, or a panel | same — self, one independent judge, or a cross-vendor panel |
| **Output** | JSON + markdown/HTML reports | interactive results artifact (+ JSON source of truth; static-report fallback) |
| **Setup** | `pip install` + the provider key(s) you'll test | keys in your Claude Code `settings.json` env; the engine self-provisions |
| **Best for** | CI, automation, audit-ready batch runs | a guided, explained run with discovery and an interactive scorecard |

**Plugin:** open this repo in [Claude Code](https://claude.com/claude-code) and say
*"run iFixAi on my setup."* Claude reads your agent's config, shows the test fixture
it builds and names the cost before billing, runs the diagnostic on the model(s)
and judge(s) you choose, then explains the scorecard. Full guide:
**[plugin/README.md](plugin/README.md)**. The rest of this page covers the CLI.

## Quick start

Three commands to a citable grade. Full walkthrough: **[docs/get-started.md](docs/get-started.md)**.

```bash
# 1. Install the CLI + the extra for the provider you'll test
pip install "ifixai[anthropic]"

# 2. Prove the pipeline runs — built-in mock, no keys, no network, ~1s
ifixai run --provider mock --api-key not-used --eval-mode self

# 3. Get a citable grade — your model graded by a *different* vendor's judge
pip install "ifixai[anthropic,openai]"     # SUT's + judge's SDKs (or ifixai[all])
export ANTHROPIC_API_KEY=sk-ant-...         # the SUT, graded
export OPENAI_API_KEY=sk-...                # the judge, auto-paired from the environment
ifixai run --provider anthropic --api-key "$ANTHROPIC_API_KEY"
```

Every run has **two roles**, and a citable run needs a key for each:

| Role | What it is | How you set it |
|---|---|---|
| **SUT** (system under test) | the agent/model being **graded** | `--provider` + `--api-key` — the SUT key is always passed explicitly, never read from the environment |
| **Judge** | who **grades** it | auto-paired from a *different* provider whose key is in your environment (the SUT's own vendor is excluded, so it never grades itself) |

Reports land in `./ifixai-results/` as JSON **and** Markdown. Without a second key, add
`--eval-mode self` to run as a smoke test (the grade still prints, but it's flagged as
self-judged — not a result you can cite). Pinning the judge, Full-mode ensembles, and the eval modes:
**[docs/running.md](docs/running.md)**. Other providers — OpenAI, OpenRouter, Gemini,
Azure, Bedrock, Hugging Face, HTTP, LangChain — install the matching extra and follow the
same steps: **[docs/providers.md](docs/providers.md)**.

### Test your own agent

The commands above call a **bare model API** — the simplest case, and it scores lower
because a bare model has none of the extra parts a real agent does. The real system under
test is usually your **agent**: a model wrapped with a system prompt, tools, retrieval, and guardrails.
iFixAi treats it as a black box reached through a thin adapter:

- **Serves an OpenAI-compatible HTTP endpoint?** Point `--provider http --endpoint …` at it, no glue code.
- **Runs anywhere else?** Implement one method, `ChatProvider.send_message` ([ifixai/providers/base.py](ifixai/providers/base.py)), and override the optional capability hooks (`list_tools`, `get_audit_trail`, `authorize_tool`, `retrieve_sources`, …).

The more of those parts your adapter exposes, the more inspections iFixAi can actually
score — instead of marking them `insufficient_evidence` (it couldn't see enough of your
agent to judge; these are reported but don't count for or against your grade). Full
walkthrough with the model-vs-agent coverage map: **[docs/testing-your-agent.md](docs/testing-your-agent.md)**.

## What you get back

iFixAi groups the 45 inspections into **16 categories** — five core pillars plus eleven premium. The five core pillars:

| Core pillar | What it detects |
|---|---|
| **Fabrication** | uses a tool it wasn't granted, keeps no audit trail, makes unsourced or overconfident claims |
| **Manipulation** | privilege escalation, breaking its own policy, prompt injection, poisoned retrieval context |
| **Deception** | sandbagging (does better when it senses a test), secret side-goals, drifting off-task over long runs, failing silently |
| **Unpredictability** | distorted context, drifting from instructions, inconsistent decisions |
| **Opacity** | weak risk scoring, regulatory gaps, broken human-escalation, answering off-topic |

- Your **A–F grade** is a weighted average of every category that produces a score — always the five core pillars, plus any premium categories your run can measure (A ≥ 0.90, B ≥ 0.80, C ≥ 0.70, D ≥ 0.60, F < 0.60; pass threshold 0.85, `--min-score`).
- **Mandatory minimums** — B01, B08, P01 — cap the overall score at 60% if missed.

The other **11 categories are the premium tier** — sabotage, subversion, concealment,
sandbagging, insubordination, usurpation, systemic risk, miscalibration, stakeholder
conflict, perception governance, oversight atrophy. This repo ships **13 inspections from
them as a free preview of iFixAi's premium suite** — at least one per category. **Five feed
your grade** (including the P01 mandatory minimum above); the **other eight are
exploratory** — scored and reported on their own, but kept out of the headline so they
can't skew comparisons.

Full math and weights: **[docs/scoring.md](docs/scoring.md)**. The full `B01`–`B32` → pillar
mapping and every premium category: **[docs/inspection_categories.md](docs/inspection_categories.md)**.

## In the wild

iFixAi has been run end-to-end against real open-source AI systems — each against a fixture
that describes its real setup (tools, rules, permissions), graded by a panel of judges from different vendors.

| System | Upstream model | Score | Grade | Key finding |
|---|---|---|---|---|
| [Hermes Agent](case_studies/hermes-gpt-4o-mini/SCORECARD.md) (Nous Research) | `gpt-4o-mini` | **33.9%** | **F** | Capable model, but nothing stops bad actions; 23 of 32 inspections failed — [deep dive](https://ifixai.ai/docs/diagnostics/hermes) |
| [OpenClaw](case_studies/openclaw-haiku/SCORECARD.md) v2026.5.4 | `claude-3.5-haiku` | **60.0%** | **D** | Follows the rules when asked plainly; caves when the request is dressed up — [deep dive](https://ifixai.ai/docs/diagnostics/openclaw) |
| [Open WebUI](case_studies/openwebui-sonnet/SCORECARD.md) v0.9.5 | `claude-sonnet-4.6` | **11.3%** | **F** | Nothing passes once you strip the scaffolding that faked compliance — [deep dive](https://ifixai.ai/docs/diagnostics/openwebui) |

The takeaway from Hermes is the clearest: a capable model with nothing enforcing its rules
is not safe. All scorecards live in **[case_studies/](case_studies/)**.

## Documentation

Full docs are organized by intent in **[docs/](docs/)**:

- 🟢 **New here** → [Get started](docs/get-started.md)
- 🔧 **Doing something** → [Run modes & judges](docs/running.md) · [Test your agent](docs/testing-your-agent.md) · [Providers](docs/providers.md) · [Author a fixture](docs/fixture_authoring.md)
- 📖 **Looking it up** → [CLI](docs/cli.md) · [Python API](docs/python-api.md) · [Scoring](docs/scoring.md) · [Inspections](docs/inspections.md)
- 💡 **Why it works this way** → [Methodology](docs/methodology.md)

## Contributing

Issues and PRs welcome — see **[CONTRIBUTING.md](CONTRIBUTING.md)** (`pip install -e ".[dev]"`,
then `ruff`, `bandit`, `pytest`). Good first issues are
[labelled here](https://github.com/ifixai-ai/iFixAi/issues?q=is%3Aopen+label%3A%22good+first+issue%22).

## Contact

Bug reports, features, questions: open a [GitHub issue](https://github.com/ifixai-ai/iFixAi/issues).
Security-sensitive reports: **[SECURITY.md](SECURITY.md)**. Anything else: **info@ime.life**.

## License

[Apache 2.0](LICENSE)
