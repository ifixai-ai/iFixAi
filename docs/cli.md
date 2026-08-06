# CLI reference

Every `ifixai` command and `ifixai run` flag. Guided first run: [get-started.md](get-started.md).

## Commands

```bash
ifixai init                  # check env for provider keys, suggest a first run
ifixai run                   # run inspections (Standard or Full mode)
ifixai run --fixture FILE    # custom fixture (YAML or JSON)
ifixai list tests            # all 45 inspections (32 core + 13 extended)
ifixai list fixtures         # registered named fixtures
ifixai validate [FILE]       # per-test layout, or a fixture against schema.json
ifixai compare A B           # diff two scorecard reports
ifixai run -p openai -k "$OPENAI_API_KEY" -c DECEPTION   # example: one category
```

## `ifixai run` flags

### System under test (SUT)

| Flag | Default | Does |
|---|---|---|
| `--provider`, `-p` | none | `mock`, `openai`, `orcarouter`, `openrouter`, `anthropic`, `gemini`, `azure`, `bedrock`, `huggingface`, `http`, `langchain`. |
| `--api-key`, `-k` | none | SUT API key. Always passed explicitly, never read from the environment. |
| `--model`, `-m` | provider default | Model identifier override. |
| `--endpoint`, `-e` | none | Endpoint URL (required for `http` and `azure`). |
| `--system-prompt`, `-s` | none | Custom system instructions sent before each inspection. |
| `--grounding` | `sut` | Governance context source: `sut` (baked-in), `fixture` (system prompt derived from fixture), `none`. |
| `--sut-temperature` | `0.0` | SUT sampling temperature. B22 needs `0` or `--sut-seed`. |
| `--sut-seed` | none | SUT sampling seed (recorded in the manifest either way). |

### Judge

| Flag | Default | Does |
|---|---|---|
| `--eval-mode` | auto | `deterministic`, `single`, `full`, or `self`. See [How a run is judged](#how-a-run-is-judged). |
| `--judge-provider` | none | Judge provider. Repeat >=2 times for a Full-mode ensemble. |
| `--judge-api-key`, `--judge-model` | none | Key(s) and model(s), paired with `--judge-provider`. |
| `--judge-budget` | `0` | Max judge LLM calls per run (`0` = unlimited). |

### Fixture and governance

| Flag | Default | Does |
|---|---|---|
| `--fixture`, `-f` | auto | Fixture name or YAML/JSON path. |
| `--governance` | none | `GovernanceFixture` YAML path; structural inspections score against your declared policies. See [fixture_authoring.md](fixture_authoring.md). |
| `--mode` | `standard` | `standard` or `full` (hand-built fixture + >=2 distinct judge providers). Deprecated alias `--profile` (`quick` → `standard`). |

### Suite subset

| Flag | Does |
|---|---|
| `--strategic` | Top 8 strategic tests only. |
| `--test`, `-b` | Test(s) by ID, repeatable: `-b B01 -b B08`. |
| `--category`, `-c` | Category name(s), case-insensitive, repeatable; beats `--strategic`. Names: `FABRICATION`, `MANIPULATION`, `DECEPTION`, `UNPREDICTABILITY`, `OPACITY`, `SABOTAGE`, `SUBVERSION`, `CONCEALMENT`, `SANDBAGGING`, `INSUBORDINATION`, `USURPATION`, `SYSTEMIC_RISK`, `MISCALIBRATION`, `STAKEHOLDER_CONFLICT`, `PERCEPTION_GOVERNANCE`, `OVERSIGHT_ATROPHY`. |

### Output and reporting

| Flag | Default | Does |
|---|---|---|
| `--output`, `-o` | `./ifixai-results/` | Report directory. |
| `--format` | `both` | `json`, `markdown`, or `both`. |
| `--name`, `--version` | provider name, `1.0` | System name and version label in reports. |
| `--min-score` | `0.85` | Exit code `2` if overall score is below (CI gate). |
| `--quiet`, `-q` | off | Suppress banner and summary; stdout still carries scores. |

### Execution and reliability

| Flag | Default | Does |
|---|---|---|
| `--timeout`, `-t` | `30` | Per-request timeout in seconds. |
| `--concurrency`, `-j` | `5` | Max in-flight LLM requests (1-20). Overrides `IFIXAI_CONCURRENCY`. |
| `--no-parallel` | off | Alias for `--concurrency 1`. |
| `--dry-run` | off | Print inspection and judge-call estimates, then exit. |
| `--reliability-out` | `runs` | Directory for `manifest.json`, one subdir per run. |
| `--run-nonce` | fresh | Replay-protection nonce (16 hex chars), recorded in the manifest. |
| `IFIXAI_JUDGE_FALLBACKS` (env) | packaged JSON | Path to the judge fallback-model chain. See [Judge fallback models](#judge-fallback-models). |
| `--holdout-seed`, `--b{12,14,28,29,30,32}-seed` | fresh random | Pin, or set matching `IFIXAI_*_SEED`, to replay a run. See [reproducibility.md](reproducibility.md). |

## How a run is judged

The SUT (system under test) is the agent being graded via the SUT flags above; its key is never read from the environment. The judge grades the SUT's answers; a citable grade needs a judge from a second, different provider.

### Judge auto-pairing

With no `--judge-*` flags, iFixAi picks a judge from a different provider whose key it finds in the environment, never the SUT's own. With only one credential it refuses unless you pass `--eval-mode self`. Preference order: [testing-your-agent.md](testing-your-agent.md#provider-reference).

### Evaluation modes (`--eval-mode`)

| Mode | What it does |
|---|---|
| `deterministic` | Structural inspections only; no judge call. |
| `single` | One cross-provider judge (`--judge-provider` required). |
| `full` | Multi-judge ensemble (>=2 `--judge-provider`; Full mode only). |
| `self` | SUT grades itself; grade prints but is flagged, not citable. |

### Standard vs Full

Same 45 inspections either way. Standard: auto fixture (the bundled default is a **seeded-defect demo** — expect 15/45 FAILs by design; see [`ifixai/fixtures/default/README.md`](../ifixai/fixtures/default/README.md)), one auto-paired judge. Full (`--mode full`): requires `--fixture` (the default fixture is refused) and >=2 judge providers; majority vote, tie-break `fail > partial > pass`.

```bash
# Standard: export a second provider key and the judge auto-pairs.
ifixai run --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN"

# Full: hand-built fixture, two independent judges.
ifixai run --mode full \
  --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN" \
  --fixture ./my-fixture.yaml \
  --judge-provider anthropic --judge-api-key "$ANTHROPIC_API_KEY" \
  --judge-provider openai    --judge-api-key "$OPENAI_API_KEY"
```

Judge design rationale: [methodology.md](methodology.md#cross-provider-judge-default).

### Judge fallback models

When the judge provider itself errors — an OpenRouter 502/503, a timeout, a dropped connection — the probe is retried on the next model in a priority-ordered fallback chain instead of killing the run. If every model is down, that one probe drops as INCONCLUSIVE and the run continues. A malformed judge reply is *not* a provider fault and never switches models.

The chain is plain JSON. Defaults ship at [`ifixai/judge/judge_fallbacks.json`](../ifixai/judge/judge_fallbacks.json) (OpenRouter: Gemini 2.5 Flash → GPT-4o mini → Haiku 4.5 → DeepSeek V3.2 → Qwen3 235B → GLM-5.2). The order is cheapest-and-tersest first: a verbose model burns real credit before it fails. Override by dropping an `ifixai.judge-fallbacks.json` next to your run, or point `IFIXAI_JUDGE_FALLBACKS` at any path:

```json
{
  "providers": {
    "openrouter": {
      "attempts_per_model": 2,
      "models": [{ "model": "z-ai/glm-5.2" }, { "model": "openai/gpt-4o-mini" }]
    }
  }
}
```

`attempts_per_model` is how many provider errors one model absorbs before the chain advances. An empty `models` list disables fallbacks; with no fallback declared, the configured judge keeps its full retry budget instead. Substitutions are counted under `fallback_grades` in the run's judge stats, so you can see which model actually graded.

The SUT's own `--model` is dropped from the chain: a fallback must never turn a declared cross-provider grade into silent self-judging. Pointing the *configured* judge at the SUT is still `--eval-mode self`, which is declared and flagged.

Cost and time controls, so a dead judge cannot drain a key or stall a run:

- Judge calls ask OpenRouter for **no reasoning tokens**. A hybrid-reasoning model asked for a verdict otherwise thinks out loud until the token ceiling cuts it off — billed in full, worthless as a verdict.
- A **truncated** reply retires that model immediately rather than re-buying the same overrun.
- A model that fails is **retired for the rest of the run**. The chain is not re-walked from the dead primary on every grade. Once all models are retired, remaining probes drop for free with no further calls.
- The scorecard reports `substitute judge graded this run:` and `judge calls that failed and were retried:` whenever either happened, so a grade always names its origin.
