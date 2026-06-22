# iFixAi documentation

Find the page that matches what you're trying to do. The docs are organized around four
needs — **learning**, **doing**, **looking up**, and **understanding**
([Diátaxis](https://diataxis.fr/)).

## 🟢 New here → tutorials

Never run iFixAi before? Start here — a single start-to-finish walkthrough that ends in a real, citable scorecard.

- **[Get started](get-started.md)** — from a clean machine to a real, citable scorecard in four steps.

## 🔧 Trying to do something → how-to guides

Task-oriented recipes for a specific goal.

- **[Run a diagnostic: roles, judges, and modes](running.md)** — what makes a grade citable, and Standard vs Full mode.
- **[Test your own agent](testing-your-agent.md)** — wire in your real agent via `--provider http` or a `ChatProvider` adapter.
- **[Set up a provider](providers.md)** — install, env vars, and run recipes for every provider.
- **[Author a fixture](fixture_authoring.md)** — declare your roles, tools, permissions, policies, and governance.
- **[Reproduce a run](reproducibility.md)** — the manifest, the digest algorithm, and verification helpers.

## 📖 Looking something up → reference

Information-oriented, consult-don't-read.

- **[CLI reference](cli.md)** — every command and `ifixai run` flag.
- **[Python API](python-api.md)** — the `ifixai.api` surface.
- **[Scoring](scoring.md)** — the formula, grade bands, thresholds, and mandatory minimums.
- **[Inspections](inspections.md)** — terse what/how rows for all 45 inspections.
- **[Inspection categories](inspection_categories.md)** — the `B01`–`B32` → pillar mapping and the extended categories.
- **[Fixture schema](../ifixai/fixtures/schema.json)** — the source-of-truth JSON Schema; see also the [fixtures README](../ifixai/fixtures/README.md).

## 💡 Wanting to understand why → explanation

Understanding-oriented background and rationale.

- **[Methodology](methodology.md)** — why the five pillars, why a cross-provider judge, what operational misalignment means, and how iFixAi compares to other eval frameworks.

## See it in practice

See the diagnostic applied end-to-end — full scorecards for real open-source agents, plus the live plugin front door.

- **[Case studies](../case_studies/)** — full scorecards for real open-source agents (Hermes, OpenClaw, Open WebUI). Deep dives at [ifixai.ai](https://ifixai.ai/docs/diagnostics/).
- **Claude Code plugin** — the zero-install front door: Claude guides the run, billed to a provider key in your Claude Code settings.
