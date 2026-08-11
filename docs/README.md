# iFixAi documentation

Find the page that matches what you're trying to do. The docs are organized around four
needs: **learning**, **doing**, **looking up**, and **understanding**
([Diátaxis](https://diataxis.fr/)).

## 🟢 New here → tutorials

- **[Get started](get-started.md)**: from a clean machine to a real, citable scorecard in four steps.

## 🔧 Trying to do something → how-to guides

- **[Test your own agent](testing-your-agent.md)**: wire in your real agent via `--provider http` or a `ChatProvider` adapter, with the provider reference.
- **[Author a fixture](fixture_authoring.md)**: declare your roles, tools, permissions, policies, and governance.
- **[Reproduce a run](reproducibility.md)**: the manifest, the digest algorithm, and verification helpers.

## 📖 Looking something up → reference

- **[CLI reference](cli.md)**: every command and `ifixai run` flag, plus judges and eval modes.
- **[Python API](python-api.md)**: the `ifixai.api` surface.
- **[Scoring](scoring.md)**: the formula, grade bands, thresholds, and mandatory minimums.
- **[Inspections](inspections.md)**: what/how rows for all 45 inspections and the pillar mapping.
- **[Fixture schema](../ifixai/fixtures/schema.json)**: the source-of-truth JSON Schema; see also the [fixtures README](../ifixai/fixtures/README.md).

## 💡 Wanting to understand why → explanation

- **[What we test, and how](what-we-test.md)**: the plain-language walkthrough, no code. Start here if you are evaluating the product rather than running it.
- **[Methodology](methodology.md)**: why the five pillars, why a cross-provider judge, what we actually measure, and how iFixAi compares to other eval frameworks.

## See it in practice

- **[Case studies](../case_studies/)**: scorecards for fixtures reconstructed from public accounts of two real incidents (Dragontail dispatch, Instagram account support). Not tests of either vendor's production system; before-remediation only. Deep dives at [ifixai.ai](https://ifixai.ai/docs/diagnostics/).
- **Claude Code plugin**: the zero-install front door. Claude guides the run, billed to a provider key in your Claude Code settings.
- **[Traction](traction.md)**: installs and runs over time.
