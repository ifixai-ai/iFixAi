# Get started

Clean machine to a citable scorecard in four steps. Flags, judges, and modes: [cli.md](cli.md).

## 1. Install

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install "ifixai[anthropic]"
```

Swap the extra for your provider ([provider reference](testing-your-agent.md#provider-reference)). `ifixai init` shows which keys are set.

## 2. Prove the pipeline runs

No keys, no network, about a second:

```bash
ifixai run --provider mock --api-key not-used --eval-mode self
```

Runs all 48 inspections and writes a report to `./ifixai-results/`. A plumbing check, not a diagnosis.

## 3. Run a real model

Prefer your real deployed agent, reached over its own HTTP endpoint:

```bash
ifixai run --provider http --endpoint <agent-url> --grounding sut
```

`--grounding sut` (the default) reads the agent's own governance; don't inject a fixture over a real agent.

No endpoint? Test the bare model. Pass the SUT key explicitly; the CLI does not read it from the environment:

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...
ifixai run --provider anthropic --api-key "$ANTHROPIC_API_KEY" --eval-mode self
```

Every run writes JSON and Markdown to `./ifixai-results/` (override with `--output`). The report shows:

- the A–F grade and the three mandatory-minimum gates (B01, B08, P01; [scoring.md](scoring.md));
- the five core pillars that set the grade, plus 11 extended categories (reported, never graded);
- `warnings[]`: inspections that returned `insufficient_evidence` instead of an invented score.

`--eval-mode self` is flagged as self-judged in the output: a smoke test, not a citable result.

## 4. Get a real, citable grade

Add a second provider's SDK extra and key, drop `--eval-mode self`; the judge auto-pairs from the environment:

```bash
pip install "ifixai[anthropic,openai]"   # or ifixai[all]
export ANTHROPIC_API_KEY=sk-ant-api03-...   # SUT: graded
export OPENAI_API_KEY=sk-...                # judge: any second, different provider
ifixai run --provider anthropic --api-key "$ANTHROPIC_API_KEY"
```

The SUT's own provider is excluded from judge selection. With no second key the run refuses rather than self-judging.

## Next steps

- Test your own agent: [testing-your-agent.md](testing-your-agent.md)
- Pin the judge or run a Full-mode ensemble: [cli.md](cli.md#how-a-run-is-judged)
- Author a domain fixture: [fixture_authoring.md](fixture_authoring.md)
- Other providers: [provider reference](testing-your-agent.md#provider-reference)
