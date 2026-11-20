# Get started

Clean machine to a citable scorecard in four steps. Flags, judges, and modes: [cli.md](cli.md).

## 1. Install

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: py -m venv .venv, then .venv\Scripts\activate
pip install ifixai
```

Add your provider's extra when you test a real model, e.g. `pip install "ifixai[openai]"` ([provider reference](testing-your-agent.md#provider-reference)). `ifixai init` shows which keys are set.

Windows: if PowerShell can't find `ifixai` after `pip install`, add Python's `Scripts\` folder to your PATH, or run it as `python -m ifixai.cli.main`.

## 2. Prove the pipeline runs

No keys, no network:

```bash
ifixai run --provider mock --eval-mode self
```

Runs the default 10 inspections on a built-in demo agent and writes a report to `./ifixai-results/`. A plumbing check, not a diagnosis: the demo agent gives unsafe replies planted in its setup, so it fails 4 of the 10 (B01, B12, B16 and P01), gets grade D and exits 2. With `--full-run` it runs 59 (B11 and M06 need a custom adapter, so they are set aside) and fails 14 ([what the demo shows](../ifixai/fixtures/default/README.md)). Every `ifixai run` on this page runs those 10; add `--full-run` for all 61 ([what the default covers](../README.md#what-a-default-run-covers)).

## 3. Run a real model

First write `my-fixture.yaml`, your agent's roles, tools and rules ([how](fixture_authoring.md)). Every run below passes it: without `--fixture`, iFixAi tests the demo agent's made-up setup, not yours.

Prefer your real deployed agent, reached over its own HTTP endpoint. Its token goes in `AGENT_API_KEY`, passed as `--api-key` (no auth: `--auth-method none` instead). A second provider grades the replies, here OpenAI:

```bash
pip install "ifixai[openai]"
export OPENAI_API_KEY=sk-...
export AGENT_API_KEY=...        # your agent's own token
ifixai run --provider http --endpoint <agent-url> --api-key "$AGENT_API_KEY" --fixture ./my-fixture.yaml --grounding sut --dry-run
```

`--dry-run` prints the estimate and stops; drop it to run. `--grounding sut` (the default) keeps the agent's own system prompt instead of injecting one built from the fixture.

No endpoint? Test the bare model. Its key is read from its environment variable:

```bash
pip install "ifixai[anthropic]"
export ANTHROPIC_API_KEY=sk-ant-api03-...
ifixai run --provider anthropic --fixture ./my-fixture.yaml --eval-mode self
```

Every run writes JSON and Markdown to `./ifixai-results/` (override with `--output`). The report shows:

- the A–F grade and the three mandatory-minimum gates (B01, B08, P01; [scoring.md](scoring.md));
- the five core pillars that set the grade, plus 20 extended categories (reported, never graded);
- `warnings[]`: inspections left INCONCLUSIVE (with why) instead of an invented score; `not_applicable[]`: checks set aside before the run.

`--eval-mode self` is flagged as self-judged in the output: a smoke test, not a citable result.

## 4. Get a real, citable grade

Add a second provider's SDK extra and key, drop `--eval-mode self`; the judge auto-pairs from the environment:

```bash
pip install "ifixai[anthropic,openai]"   # or ifixai[all]
export ANTHROPIC_API_KEY=sk-ant-api03-...   # SUT: graded
export OPENAI_API_KEY=sk-...                # judge: any second, different provider
ifixai run --provider anthropic --fixture ./my-fixture.yaml
```

The SUT's own provider is excluded from judge selection. With no second key the run refuses rather than self-judging. Recommended judge models and costs: [cli.md](cli.md#recommended-judge-setups).

## Guided wizard

Prefer menus to flags? `ifixai setup` is an arrow-key wizard: pick provider, model, judge and suite, and it writes `ifixai.yaml`. After that, `ifixai run` needs only your fixture (the wizard offers the built-in ones).

```bash
ifixai setup
ifixai run --fixture ./my-fixture.yaml
```

The wizard detects API keys already in your environment and shows them at the top of each prompt. No key found? It tells you which env var to export; if it's still missing when you run, you're prompted for it before the first API call. What `ifixai.yaml` holds: [cli.md](cli.md#reusable-config).

## Run it from your coding agent

Your coding agent can be the operator: it finds your agent, builds the fixture, shows the cost via a free `--dry-run`, runs only after you say yes, and explains the scorecard. Install iFixAi and run any command once:

```bash
pip install ifixai   # or, with nothing installed: uvx ifixai --help
ifixai --help
```

That first run adds the iFixAi skill (`ifixai/SKILL.md`) to your coding agent and prints one line saying so. Then ask your agent to *"run iFixAi on my agent"*: it loads the skill by its description, with no slash command to remember, in every repo you open.

Where it goes: run from a terminal, `~/.agents/skills`, the folder most coding agents share, plus `~/.claude/skills` when Claude Code is installed, since Claude Code reads only its own. Run by a coding agent, only the folder that agent reads, so an agent with its own folder gets the skill the first time it runs `ifixai`. `ifixai install --user` adds it for every coding agent it finds, and `ifixai install --list` shows each one's folder.

It runs again when iFixAi is upgraded, to refresh the text. It never writes over a `SKILL.md` it didn't write; `ifixai install --user --force` replaces yours. `ifixai install --revert` removes it and keeps it off, `IFIXAI_NO_SKILL=1` turns it off, and it never runs in CI.

Optional, per project: `ifixai install` writes the same instructions into the current repo as a native `/ifixai` command, for teammates who don't have iFixAi installed. It needs only `uv` and Python 3.10+, no API key:

```bash
uvx ifixai install --agents cursor   # or any slug from --list
uvx ifixai install --agents all      # every agent at once
uvx ifixai install --list            # every supported agent and where its file lands
```

On a new project, name the agent with `--agents` (auto-detect only finds agents whose folder already exists). `--agents codex` writes the user-level skill, since Codex reads skills rather than project commands.

## Three ways to run

All three run the same engine. The difference is how you drive it.

| | **Guided wizard** | **Explicit flags** | **Skill in your coding agent** |
|---|---|---|---|
| **How you drive it** | `ifixai setup` once, then `ifixai run` with no flags; config saved to `ifixai.yaml` | every option as a flag; fully scriptable | your coding agent is the operator: finds your setup, builds the fixture, runs it, explains the scorecard |
| **Best for** | first-time users, repeatable runs, team onboarding | CI, automation, scripted batches | a guided, explained run inside the agent you already use |
| **Keys** | detected by the wizard; `ifixai.yaml` stores the env-var name, never the secret | each provider's env var, or `--api-key` | same |
| **What you test** | any provider, or your agent's real endpoint | same | same |
| **Who grades it** | the model itself, one independent judge, or a panel of judges | same | same |
| **Output** | JSON + Markdown reports + terminal scorecard | same | same, plus an interactive results page |
| **Suite** | picked in the wizard | 10 inspections by default; `--full-run` for all 61, or `--suite` ([options](cli.md#suite-options)) | the agent picks, same engine |
| **Works in** | any terminal | any terminal or CI | any coding agent that reads Agent Skills |

## Next steps

- Test your own agent: [testing-your-agent.md](testing-your-agent.md)
- Pin the judge or run a Full-mode ensemble: [cli.md](cli.md#how-a-run-is-judged)
- Author a domain fixture: [fixture_authoring.md](fixture_authoring.md)
- Other providers: [provider reference](testing-your-agent.md#provider-reference)
