# Contributing to ifixai

Thanks for considering a contribution. This guide covers the mechanics of adding inspections, fixtures, and providers.

## Environment setup

```bash
git clone <your-fork-url> ifixai
cd ifixai
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

The `pre-commit install` step wires up local hooks (`gitleaks`, `ruff`, and a `.env` guard). Run them on demand with `pre-commit run --all-files`.

Verify:

```bash
ruff check ifixai
bandit -r ifixai -ll
```

The package ships `ifixai/` plus the per-inspection bundles under `ifixai/inspections/b<NN>_<slug>/`.

## Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs on **every push** to any branch that contains the workflow file.

For **pull requests**, the same checks also run when the PR **base branch** is `main`, `fix/**`, or `feat/**`. A PR targeting another branch (for example `develop` or `release/1.0`) does not receive a `pull_request` workflow run; you may still see a recent run from **pushes** to the PR head branch—open the **Actions** tab and filter by branch if needed.

## Adding a test (inspection)

Each inspection lives in its own folder under `ifixai/inspections/bNN_short_name/`. Required contents:

- `runner.py` — declares `SPEC` and the `BaseTest` subclass
- `definition.yaml` — the conversation plan (steps, prompts, evaluation hints)
- `rubric.yaml` — analytic-judge dimensions and weights (default; loaded when no outcome-type-specific file exists)
- `rubric_{outcome_type}.yaml` — optional outcome-type-specific rubric (e.g. `rubric_comply.yaml`); loaded in preference to `rubric.yaml` when the inspection requests that outcome type. Use this when a single inspection covers both `refuse` and `comply` cases that require different scoring dimensions.
- `references.yaml` — reference responses used by atomic-claims grounding
- `corpus.yaml` — adversarial seeds (only B12, B14, B28, B30)

The minimum contract:

1. Declare the `SPEC`, an `InspectionSpec` instance with `test_id`, `name`, `category` (one of the five `InspectionCategory` values), `description`, `threshold`, `weight`, `scoring_method`, and optional `is_strategic` / `is_mandatory_minimum` flags. The canonical test → pillar list is [`docs/inspections.md`](docs/inspections.md#categories); update that table when you add or recategorise an inspection.
2. Implement a subclass of `BaseTest` (from `ifixai.harness.base`). Override `run()` to produce a list of `EvidenceItem`s. Use `self.pipeline.evaluate(...)` to get a pass/fail from the configured judge.
3. Declare `required_fixture_keys: frozenset[str]` on the subclass listing every fixture key the inspection's templates reference. The fixture loader validates this at load time; inspections that reference keys the fixture doesn't provide fail fast with an actionable error.
4. Render every prompt through `ifixai.utils.template_renderer.render(template, context)`. Direct `str.format(...)` or f-string interpolation on fixture values is forbidden — it silently leaks `{placeholder}` literals to the model when a key is missing.
5. Register the inspection in `ifixai/harness/registry.py` (import the class and `SPEC` from `ifixai.inspections.bNN_short_name.runner` + add to `ALL_SPECS` + `create_inspection` switch).
6. Update `ifixai/scoring/category_weights.py` only if the inspection belongs to the strategic set.
7. Run `ifixai validate` (no args) — the layout validator will fail loudly if any required artifact is missing or the folder name disagrees with the YAML `test_id`.

### Inspection self-validation

Run `ifixai validate` (no args) after authoring or editing an inspection. The layout validator checks:

- the per-test folder contains every required artifact (`runner.py`, `definition.yaml`, `rubric.yaml`, `references.yaml`, plus `corpus.yaml` for B12/B14/B28/B30); optional `rubric_{outcome_type}.yaml` files are not validated for presence but must pass schema checks if present;
- the folder name agrees with the YAML `test_id`;
- `SPEC` invariants (id, category, threshold, weight, strategic flags) match the registry;
- the runner registers cleanly with `harness/registry.py`.

## Authoring a fixture

Fixtures are YAML files under `ifixai/fixtures/`. Validate against `ifixai/fixtures/schema.json`. A fixture MUST supply every key listed in the union of every registered inspection's `required_fixture_keys`.

The `x-placeholders` section of the schema (lint-only) enumerates the placeholder keys any inspection may reference. Keep it in sync when you add an inspection that references a new key.

Three example fixtures live under `ifixai/fixtures/examples/`. Copy one as a starting point.

## Registering a new provider

Providers implement the `ChatProvider` protocol from `ifixai/providers/base.py`. Steps:

1. Create `ifixai/providers/<your_provider>.py` implementing at minimum `send_message` — other capability methods may raise `NotImplementedError` if the provider does not expose them.
2. Register the provider string in `ifixai/providers/resolver.py`.
3. Add an optional dependency extra in `pyproject.toml` under `[project.optional-dependencies]` so users install only what they need.
4. Do not swallow exceptions silently. If the provider has idiomatic error types, translate them into `ProviderError` (or a subclass).
5. Exercise the provider end-to-end against a fixture before opening a PR (`ifixai run --provider <your_provider> ...`); attach the resulting scorecard snippet to the PR body.

## Running checks locally

```bash
# lint
ruff check ifixai

# type check
mypy ifixai

# security scan
bandit -r ifixai -ll

# layout / inspection validation
ifixai validate
```

## Commit conventions

Follow the Conventional Commits-style prefixes used by this project:

- `feat:` new user-visible feature
- `fix:` bug fix
- `refactor:` behaviour-preserving change
- `docs:` documentation only
- `chore:` tooling / housekeeping
- `perf:` performance improvement
- `ci:` CI configuration

Keep commits small and atomic.

## Pull requests

- Target branch: `main`.
- Include a test plan in the PR body.
- Confirm `ruff`, `bandit`, and `ifixai validate` all pass locally before requesting review. CI runs `ifixai validate` (layout) plus `ifixai validate` on each file under `ifixai/fixtures/examples/`. `mypy` is advisory (run it locally if you touched typed surfaces, but it is not a CI gate).
- For any inspection / fixture / provider change, paste one worked example scorecard snippet (JSON or Markdown) into the PR body.

## Where to ask

Open a GitHub issue on the repository for questions, bug reports, or feature proposals. For security-sensitive reports see `SECURITY.md`.
