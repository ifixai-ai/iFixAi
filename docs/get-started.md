# Get started

From a clean machine to a real, citable scorecard in four steps. Each step stands on its
own — copy, paste, run. For the concepts behind it (SUT, judge, modes) see
[running.md](running.md); for every flag see [cli.md](cli.md).

> **Only one vendor's key?** You can still run every step below as a smoke test with
> `--eval-mode self` — you just won't get a citable cross-vendor grade (step 4) until you
> add a second provider's key. **No keys at all?** Step 2 runs against the built-in `mock`
> with no keys and no network — a plumbing check, not a diagnosis.

## 1. Install

Install the CLI plus the extra for the provider you'll test (Anthropic/Claude here; see
[providers.md](providers.md) for the rest). Work inside a venv:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install "ifixai[anthropic]"
```

Run `ifixai init` any time to see which provider keys are set in your environment.

## 2. Prove the pipeline runs

Run against the built-in `mock` — no keys, no network, about a second:

```bash
ifixai run --provider mock --api-key not-used --eval-mode self
```

All 45 inspections run with a per-inspection PASS / FAIL / inconclusive line and a bar per
category, and a full report is written under `./ifixai-results/`. The mock implements every
surface, so this is a **plumbing check — not a diagnosis of any real system.**

## 3. Run a real model

Point iFixAi at a real model and you get your first real scorecard — per-inspection
scores, the five core pillars, an A–F grade. Pass the SUT key explicitly; the CLI
deliberately does not read it from the environment. (Omitting `--fixture` uses the built-in
**default** fixture; see [fixtures](../ifixai/fixtures/README.md).)

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...
ifixai run --provider anthropic --api-key "$ANTHROPIC_API_KEY" --eval-mode self
```

Every run writes JSON **and** Markdown to `./ifixai-results/` (override with `--output`).
The report shows:

- an **overall grade** (A–F) and score, plus the three **mandatory-minimum** gates
  (B01, B08, P01; see [scoring.md](scoring.md));
- a score for each of the **five core pillars** that set the grade — fabrication,
  manipulation, deception, unpredictability, opacity — **plus** a separate per-category
  score for the 11 **extended** categories, reported alongside but excluded from the
  headline grade;
- a **`warnings[]`** list naming every inspection that came back `insufficient_evidence`:
  your agent exposed no surface to measure there, so iFixAi refused to invent a score.

`--eval-mode self` keeps this to a single key, so it's a smoke test, not a verdict: the
grade still prints, but the terminal **flags it as self-judged** — a model grading itself
isn't a result you can cite. You get the full per-inspection breakdown either way.

## 4. Get a real, citable grade

By default iFixAi grades your agent with a *second, different* provider so it isn't judging
itself — so you need that provider's **SDK extra installed** (step 1 only pulled
Anthropic's) **and** its key. Add the extra, export a second key, and drop `--eval-mode
self` — the judge auto-pairs:

```bash
pip install "ifixai[anthropic,openai]"   # add the judge provider's SDK (or ifixai[all])
export ANTHROPIC_API_KEY=sk-ant-api03-...
export OPENAI_API_KEY=sk-...              # the judge — any second, different provider's key
ifixai run --provider anthropic --api-key "$ANTHROPIC_API_KEY"
#          └────────── SUT: graded ──────────┘   (judge auto-paired to openai from the env)
```

Here **anthropic is the SUT** and **openai is the judge**, auto-discovered from
`OPENAI_API_KEY`. `ANTHROPIC_API_KEY` is in the environment too, but iFixAi excludes the
SUT's own provider from judge selection, so it never grades itself. With no second key the
run refuses rather than silently self-judging.

## Next steps

- **Test your own agent** (not just a bare model): [testing-your-agent.md](testing-your-agent.md)
- **Pin the judge, or run a Full-mode ensemble:** [running.md](running.md)
- **Author a domain fixture:** [fixture_authoring.md](fixture_authoring.md)
- **Other providers:** [providers.md](providers.md)
