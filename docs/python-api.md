# Python API

`ifixai.api` runs the same engine as the CLI. The run functions are async; the
inspection/fixture/compare helpers are sync.

```python
import asyncio
from ifixai.api import (
    run_inspections, run_strategic, run_selected, run_single,
    compare_scorecards, list_tests, list_fixtures,
)

result = asyncio.run(run_inspections(
    provider="openai",
    api_key="sk-...",
    model="gpt-4o",
    fixture="default",
    system_name="my-agent",
))
print(result.overall_score, result.grade)
```

## Functions

Seven entry points: four async `run_*` functions that execute tests and return a `TestRunResult`, plus three sync helpers for comparing and listing.

| Function | Purpose |
|---|---|
| `run_inspections(...)` | Run all registered inspections (async) → `TestRunResult` |
| `run_strategic(...)` | Run the top 8 strategic tests (async) |
| `run_selected(test_ids, ...)` | Run a chosen list of test IDs (async) |
| `run_single(test_id, ...)` | Run a single test by ID (async) |
| `compare_scorecards(baseline, enhanced)` | Vendor-neutral comparison report (sync) |
| `list_tests()` | Return all `InspectionSpec` definitions (sync) |
| `list_fixtures()` | Return built-in fixture names (sync) |

## Common parameters

The run functions share the CLI's surface (see [cli.md](cli.md) for full semantics):

| Parameter | Default | Notes |
|---|---|---|
| `provider` | — | A provider name (`"openai"`, `"anthropic"`, …) **or** a `ChatProvider` instance. |
| `api_key` | `""` | SUT key. |
| `fixture` | `"default"` | A registered name or a `Fixture` object. |
| `model`, `endpoint`, `system_prompt` | `None` | SUT overrides. |
| `system_name`, `system_version` | `""`, `"1.0"` | Labels for the report. |
| `timeout`, `max_retries` | `30`, `3` | Per-request controls. |
| `judge_config`, `pipeline_config` | `None` | `JudgeConfig` / `EvaluationPipelineConfig` for judge mode, ensemble, budgets. |
| `sut_temperature`, `sut_seed`, `run_nonce`, `holdout_ids` | — | Determinism / replay controls (mirror the CLI seeds). |

## Custom providers

To test an agent that isn't a hosted model, implement `ChatProvider` from
[../ifixai/providers/base.py](../ifixai/providers/base.py) and pass the instance as
`provider`. Override the optional capability hooks (`list_tools`, `get_audit_trail`,
`authorize_tool`, `retrieve_sources`, …) to expose your agent's structural surfaces.
Walkthrough: [testing-your-agent.md](testing-your-agent.md).
