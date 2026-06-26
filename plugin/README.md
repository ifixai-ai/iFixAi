# iFixAi: Claude Code plugin

Let Claude run iFixAi's operational-misalignment diagnostic on your own agent. It discovers your
setup, builds the test fixture, names the cost before anything is billed, runs the diagnostic on
the model(s) and judge(s) you pick, then walks you through the scorecard. No flags or fixtures to
write.

## Install

From inside [Claude Code](https://claude.com/claude-code):

```
/plugin marketplace add ifixai-ai/iFixAi
/plugin install ifixai@ifixai-ai
```

Restart Claude Code or run `/reload-plugins` if it doesn't show up right away.

## Run

Just ask Claude in plain English:

> run iFixAi on my setup

or type the slash command `/ifixai:ifixai`.

Claude takes it from there. Full project docs: https://github.com/ifixai-ai/iFixAi#readme
