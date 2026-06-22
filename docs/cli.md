# CLI reference

Every `ifixai` command and the flags for `ifixai run`. For a guided first run see
[get-started.md](get-started.md); for the SUT/judge and Standard-vs-Full walkthrough
see [running.md](running.md).

## Commands

Five commands cover the whole tool — `init`, `run`, `list`, `validate`, and `compare`:

```bash
ifixai init                    # check env for provider keys, suggest a first run
ifixai run                     # run inspections (Standard or Full mode)
ifixai run --fixture FILE      # run with a custom fixture (YAML or JSON)
ifixai list tests              # list all 45 inspections (32 core + 13 extended)
ifixai list fixtures           # list registered named fixtures (examples/ load by path)
ifixai validate                # validate the per-test layout (45 folders)
ifixai validate FILE           # validate a fixture against schema.json
ifixai compare A B             # diff two scorecard reports
```

## `ifixai run` options

The `run` flags fall into six groups — the system under test, the judge, fixture and governance, suite selection, output and reporting, and execution and reliability — each a table below.

### System under test (SUT)

| Flag | Default | What it does |
|---|---|---|
| `--provider`, `-p` | — | Provider to grade: `mock`, `openai`, `openrouter`, `anthropic`, `gemini`, `azure`, `bedrock`, `huggingface`, `http`, `langchain`. |
| `--api-key`, `-k` | — | SUT API key. **Always passed explicitly** — never read from the environment. |
| `--model`, `-m` | provider default | Model identifier override. |
| `--endpoint`, `-e` | — | Custom API endpoint URL (required for `http` and `azure`). |
| `--system-prompt`, `-s` | — | Custom system instructions sent before each inspection. |
| `--grounding` | `sut` | Where the SUT gets governance context: `sut` (baked-in, for deployed agents), `fixture` (derive a system prompt from the fixture — for testing a vanilla LLM's rule-following), `none`. |
| `--sut-temperature` | `0.0` | Sampling temperature sent to the SUT. B22 needs `0` or `--sut-seed`. |
| `--sut-seed` | — | Sampling seed sent to the SUT (recorded in the manifest either way). |

### Judge

| Flag | Default | What it does |
|---|---|---|
| `--eval-mode` | auto | `deterministic` (structural only, no judge), `single` (one cross-provider judge), `full` (≥2-judge ensemble, Full mode), `self` (SUT grades itself — biased smoke test). Omitted: auto-pairs cross-provider if ≥2 credentials exist, else refuses unless `self`. |
| `--judge-provider` | — | Judge provider. Pass once for single-judge; pass ≥2 times for a Full-mode ensemble. |
| `--judge-api-key` | — | Key(s) for the judge provider(s). Pair with `--judge-provider`. |
| `--judge-model` | — | Model(s) for the judge provider(s). Pair with `--judge-provider`. |
| `--judge-budget` | `0` | Max judge LLM calls for the whole run (`0` = unlimited). |

### Fixture & governance

| Flag | Default | What it does |
|---|---|---|
| `--fixture`, `-f` | auto | Fixture name or YAML/JSON path. Omitted: auto-discovers or asks. |
| `--governance` | — | Path to a `GovernanceFixture` YAML; composes `GovernanceMixin` onto the provider so structural inspections score against your declared policies. See [fixture_authoring.md](fixture_authoring.md). |
| `--mode` | `standard` | `standard` (CI-friendly default) or `full` (hand-built fixture + ≥2 distinct judge providers required). |

### Selecting a subset of the suite

| Flag | What it does |
|---|---|
| `--strategic` | Run only the top 8 strategic tests. |
| `--test`, `-b` | Run specific test(s) by ID; repeat to select several (`-b B01 -b B08`). |
| `--category`, `-c` | Run every test in one or more categories by name; case-insensitive; repeatable; takes precedence over `--strategic`. |

```bash
ifixai run --provider anthropic --api-key "$ANTHROPIC_API_KEY" --strategic     # top 8 only
ifixai run --provider openai    --api-key "$OPENAI_API_KEY" -b B01             # single test
ifixai run --provider openai    --api-key "$OPENAI_API_KEY" -b B01 -b B08      # subset by ID
ifixai run --provider openai    --api-key "$OPENAI_API_KEY" -c DECEPTION       # one category
ifixai run --provider openai    --api-key "$OPENAI_API_KEY" -c DECEPTION -c OPACITY
```

Category names accepted by `-c/--category` (case-insensitive): `FABRICATION`,
`MANIPULATION`, `DECEPTION`, `UNPREDICTABILITY`, `OPACITY`, `SABOTAGE`, `SUBVERSION`,
`CONCEALMENT`, `SANDBAGGING`, `INSUBORDINATION`, `USURPATION`, `SYSTEMIC_RISK`,
`MISCALIBRATION`, `STAKEHOLDER_CONFLICT`, `PERCEPTION_GOVERNANCE`, `OVERSIGHT_ATROPHY`.

### Output & reporting

| Flag | Default | What it does |
|---|---|---|
| `--output`, `-o` | `./ifixai-results/` | Directory to save reports. |
| `--format` | `both` | `json`, `markdown`, or `both`. |
| `--name` | provider name | System name shown in reports. |
| `--version` | `1.0` | Version label for reports. |
| `--min-score` | `0.85` | Minimum overall score; exit code `2` if below (for CI gates). |
| `--quiet`, `-q` | off | Suppress the banner and the post-run summary; stdout still carries scores so CI gates work. |

### Execution & reliability

| Flag | Default | What it does |
|---|---|---|
| `--timeout`, `-t` | `30` | Per-request timeout in seconds. |
| `--concurrency`, `-j` | `5` | Max in-flight LLM requests (1–20). Overrides `IFIXAI_CONCURRENCY`. |
| `--no-parallel` | off | Alias for `--concurrency 1` (debug / reproducibility bisection). |
| `--dry-run` | off | Print estimated inspection and judge-call counts, then exit without API calls. |
| `--reliability-out` | `runs` | Directory for `manifest.json`, one subdir per run. |
| `--run-nonce` | fresh | Replay-protection nonce (16 hex chars); recorded in the manifest. |

**Replay seeds.** `--holdout-seed` (B01/B04/B16) and the per-inspection corpus seeds
`--b12-seed`, `--b14-seed`, `--b28-seed`, `--b29-seed`, `--b30-seed`, `--b32-seed`
default to a fresh random value each run and can be pinned (or set via the matching
`IFIXAI_*_SEED` env var) to reproduce a prior run exactly. See
[reproducibility.md](reproducibility.md).

> `--profile {quick,full}` is a **deprecated** alias for `--mode` (`quick → standard`,
> `full → full`), accepted for one release for migration.
