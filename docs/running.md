# Running a diagnostic: roles, judges, and modes

A grade is citable only when a second, different provider grades your agent — so every
real run has two roles (the agent and the judge), and the run mode (Standard or Full)
sets the rigour. For your first run, start with
[get-started.md](get-started.md). For every flag, see [cli.md](cli.md).

## The two roles in every run

Every run has two independent roles, and a citable run needs a key for each:

| Role | What it is | How you set it |
|---|---|---|
| **SUT** — *system under test* | the agent or model being **graded** | `--provider` + `--api-key` (plus optional `--model`, `--endpoint`, `--system-prompt`). The SUT key is **always passed explicitly**; the CLI never reads it from the environment. |
| **Judge** | who **grades** the SUT's answers | **Auto-paired by default:** iFixAi picks a *second, different* provider whose key it finds in your environment, and excludes the SUT's own provider so the judge is never the same vendor. Override with `--judge-provider` / `--judge-api-key` / `--judge-model`. |

So a citable run needs **two keys**: the SUT's (via `--api-key`) and the judge's (found
in the environment, or passed with `--judge-api-key`). `--eval-mode self` puts both roles
on the SUT — it grades itself — which is why a self-judged grade isn't citable; the
terminal still prints it but flags it as self-judged.

## How the judge is chosen

With no `--judge-*` flags, iFixAi auto-pairs a single judge from a *different* provider
whose credential it finds in the environment (the SUT's own provider is excluded, so it
never grades itself). With only one credential present it refuses to run unless you pass
`--eval-mode self`.

To pin the judge — or to set **two or more** judges for a Full-mode ensemble — name them
explicitly, repeating the trio once per judge:

```bash
ifixai run --provider anthropic --api-key "$ANTHROPIC_API_KEY" \
  --judge-provider openai --judge-api-key "$OPENAI_API_KEY" --judge-model gpt-4o
```

The auto-pairing preference order is documented in [providers.md](providers.md).

## Evaluation modes (`--eval-mode`)

`--eval-mode` picks who grades and how many — from no judge at all to a cross-vendor
ensemble; only the cross-provider modes produce a citable grade.

| Mode | What it does |
|---|---|
| `deterministic` | Structural inspections only; **no judge** call. |
| `single` | One cross-provider judge (`--judge-provider` required). |
| `full` | Multi-judge ensemble (≥2 `--judge-provider` flags; Full mode only). |
| `self` | SUT grades itself — biased smoke test; the grade prints but is flagged as self-judged. |

Omit `--eval-mode` and Standard mode auto-detects: with ≥2 distinct credentials it
auto-pairs cross-provider; with one, it refuses unless you pass `self`.

## Standard vs Full mode

Both modes run the **same inspections against the same agent** — the mode changes the
_rigour_, not what runs. What differs is how the fixture is built and how the verdict is
judged.

| | **Standard** (default) | **Full** (`--mode full`) |
|---|---|---|
| **Fixture** | auto: the built-in default fixture, or one you build interactively / by discovery | a hand-built, domain-specific fixture you supply with `--fixture`; the default fixture is **refused** |
| **Judge** | a **single** judge, auto-paired from a different provider when ≥2 credentials are present, otherwise a self-judge (with a `self-judge bias` warning) only if you pass `--eval-mode self` | an **ensemble of ≥2 distinct judge providers**: simple-majority aggregation, conservative tie-break (`fail > partial > pass`), every judge's verdict recorded per-inspection in the manifest |
| **Inspections run** | full suite (32 core + 13 extended), or a subset via flags | identical suite |
| **Produces** | the letter grade in a few minutes | the same grade, hardened so neither a single judge nor self-judging decides it |
| **Use for** | CI, drift tracking, sanity checks | vendor comparisons, internal review, pre-audit spot checks |

```bash
# Standard: your agent as the SUT, judged by a different provider.
# Export a second provider key (e.g. ANTHROPIC_API_KEY) and it auto-pairs the judge.
ifixai run --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN"

# Full: the same agent, a hand-built fixture, and an ensemble of TWO independent judges.
ifixai run --mode full \
  --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN" \
  --fixture ./my-fixture.yaml \
  --judge-provider anthropic --judge-api-key "$ANTHROPIC_API_KEY" \
  --judge-provider openai    --judge-api-key "$OPENAI_API_KEY"
```

In the Full example the **SUT** is the `http` agent and the two `--judge-provider` lines
are the **two judges** in the ensemble. Swap `--provider http …` for
`--provider openai --api-key …` to test a bare model instead.

How the judge paths are designed and defended:
[methodology.md](methodology.md#cross-provider-judge-default).

## Reproducibility

Every run writes a content-addressed manifest to `runs/<run_id>/manifest.json` capturing
every input. See [reproducibility.md](reproducibility.md) for the digest algorithm and
verification helpers.
