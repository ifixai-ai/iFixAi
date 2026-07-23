# Python API

`ifixai.api` runs the same engine as the [CLI](cli.md). Run functions are async; helpers are sync.

```python
import asyncio
from ifixai.api import run_inspections

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

| Parameter | Default | Notes |
|---|---|---|
| `provider` | required | Provider name (`"openai"`, `"anthropic"`, …) or a `ChatProvider` instance. |
| `api_key` | `""` | SUT key. |
| `fixture` | `"default"` | Registered name or a `Fixture` object. |
| `model`, `endpoint`, `system_prompt` | `None` | SUT overrides. |
| `system_name`, `system_version` | `""`, `"1.0"` | Report labels. |
| `timeout`, `max_retries` | `30`, `3` | Per-request controls. |
| `judge_config`, `pipeline_config` | `None` | `JudgeConfig` / `EvaluationPipelineConfig` for judges, ensemble, budgets. |
| `sut_temperature`, `sut_seed`, `run_nonce`, `holdout_ids` | `0.0`, `None`, `None`, `None` | Determinism / replay controls (mirror CLI seeds). |

To test a custom agent, implement `ChatProvider` ([../ifixai/providers/base.py](../ifixai/providers/base.py)) and pass the instance as `provider`. Walkthrough: [testing-your-agent.md](testing-your-agent.md).
