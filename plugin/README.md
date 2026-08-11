# iFixAi plugin (Claude Code and Codex)

Let your coding agent run an independent, open-source iFixAi audit on your own agent: is it
doing the job it's supposed to do, given your business rules and org structure? It discovers your
setup, builds the test fixture, names the cost before anything is billed, runs the audit on the
model(s) and judge(s) you pick, then walks you through the scorecard. No flags or fixtures to
write.

## Install

**Claude Code** — from inside [Claude Code](https://claude.com/claude-code):

```
/plugin marketplace add ifixai-ai/iFixAi
/plugin install ifixai-community@ifixai-community
```

Restart Claude Code or run `/reload-plugins` if it doesn't show up right away.

**Codex** — in your terminal:

```
codex plugin marketplace add ifixai-ai/iFixAi
codex plugin add ifixai-community@ifixai-community
```

Codex asks once to trust the plugin's hook, then provisions the engine on the first session.

## Run

Just ask your agent in plain English:

> run iFixAi on my setup

In Claude Code you can also type the slash command `/ifixai-community:ifixai`.

Your agent takes it from there. Full project docs: https://github.com/ifixai-ai/iFixAi#readme
