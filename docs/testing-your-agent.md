# Test your own agent

iFixAi tests the system you deploy: model plus tools, retrieval, and governance. A bare
model API (`--provider openai` / `anthropic` / ...) scores 33 of 45 inspections; the rest
return `insufficient_evidence` and drop out. Flags and extras: [provider reference](#provider-reference).

## Path 1: HTTP endpoint

Your agent serves `POST /v1/chat/completions`:

```bash
ifixai run --provider http --endpoint http://localhost:8000/v1 \
  --api-key "$YOUR_TOKEN" --model your-agent --eval-mode self
```

To score B28 (RAG context integrity), return a `sources` array next to `choices`:

```jsonc
{ "choices": [ { "message": { "role": "assistant", "content": "..." } } ],
  "sources": [ { "document_uri": "kb://policy/42", "text": "...", "relevance_score": 0.91 } ] }
```

Custom auth: set `IFIXAI_EXTRA_HEADERS` to a JSON object; pick `auth_method`
(`bearer` / `api_key` / `basic` / `none`) via the [Python API](python-api.md).

## Path 2: `ChatProvider` adapter

For an in-process agent, subclass [`ChatProvider`](../ifixai/providers/base.py). Only
`send_message` is required; hooks default to `None` (`insufficient_evidence`). Pass your
instance through the [Python API](python-api.md) and run as usual.

```python
from ifixai.providers.base import ChatProvider
from ifixai.core.types import ChatMessage, ProviderConfig

class MyAgentProvider(ChatProvider):
    async def send_message(self, messages: list[ChatMessage], config: ProviderConfig) -> str:
        reply = await my_agent.ainvoke([{"role": m.role, "content": m.content} for m in messages])
        return reply.text
```

| Capability hook | Makes measurable |
|---|---|
| `list_tools` / `invoke_tool` / `authorize_tool` | B01 + tool-calling checks |
| `get_governance_architecture` / `apply_override` | B02 / B04 |
| `get_configuration_version` / `get_audit_trail` | B23 / audit trail |
| `retrieve_sources` | B05 |
| `get_confidence` / `route_to_human` / `reconcile_outcome` | C02 / C05 / C11 |
| `evaluate_deployment_gate` / `evaluate_confirmation_gate` | X04 / X11 |

IDs and structural requirements: [inspections.md](inspections.md#categories), [fixture_authoring.md](fixture_authoring.md).

## No code: declare governance in the fixture

B02 / B04 / B11 / B23 / B26 / B27 can score from a declared policy, most trustworthy first:

- `--governance <path>` (recommended): external `GovernanceFixture` YAML, wrapped in automatically.
- Inline `governance:` block on the diagnostic fixture.
- `governance: { synthesize: true }` (last resort): derived from `tools` / `permissions` / `roles`.

The scorecard adds a `warnings[]` note: declared, not measured at runtime. Fields and passing values: [fixture_authoring.md](fixture_authoring.md).

## Which judge?

`--eval-mode self` is a smoke test, flagged `self-judge bias`. By default a second,
different provider grades the SUT, so nothing scores itself:

- Default judge: any non-SUT provider key in your env, on that provider's default model.
- Multiple keys: tiebreaker order `anthropic → openai → atlascloud → gemini → openrouter → azure → bedrock → huggingface`.
- No non-SUT key: the run refuses unless you pass `--eval-mode self`.
- Override: `--judge-provider` / `--judge-api-key` / `--judge-model`. Pin these for
  `openrouter` and `azure`: auto-routing can land SUT and judge on the same vendor.

For a real verdict:

```bash
# Standard: one cross-provider judge (export a second provider key, or pin --judge-provider).
ifixai run --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN"

# Full: hand-built fixture + two or more independent judges.
ifixai run --mode full \
  --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN" \
  --fixture ./my-fixture.yaml \
  --judge-provider anthropic --judge-api-key "$ANTHROPIC_API_KEY" \
  --judge-provider openai    --judge-api-key "$OPENAI_API_KEY"
```

Modes: [cli.md](cli.md#standard-vs-full). Judges: [methodology.md](methodology.md#cross-provider-judge-default).

## Coverage

| SUT shape | Inspections scored |
|---|---|
| Vanilla LLM, default fixture (ships `governance:`) | 45 / 45 \* |
| Vanilla LLM, custom fixture without governance | 33 / 45 (27 core + 6 extended) |
| `--provider mock` (zero credentials) | 45 / 45 \* |
| Every hook exposed, or full mode + multi-judge | 45 / 45 |

\* The bundled default fixture (NimbusForge, `managed_it_infrastructure`) declares a
specific domain so B32 scores, and it carries **seeded defects**: expect 15/45 FAILs
by design on a default-fixture run — they are properties of the fixture, not your
system. Defect map: [`ifixai/fixtures/default/README.md`](../ifixai/fixtures/default/README.md).
On a custom fixture, set a specific `metadata.domain` (or `metadata.on_topic_examples`)
to score B32.

## Provider reference

Install the extra first, e.g. `pip install -e ".[gemini]"`. The CLI never auto-reads
the SUT key from the environment: pass `--api-key` / `-k`, or enter it when prompted.

| `--provider` | Extra | SUT credential | Example flags |
|---|---|---|---|
| `http` (recommended) | none | your server token | `--provider http --endpoint http://localhost:8000/v1 --grounding sut -k TOKEN --model your-model-id` |
| `anthropic` | `.[anthropic]` | `ANTHROPIC_API_KEY` | `--provider anthropic -k "$ANTHROPIC_API_KEY" --model claude-sonnet-4-6` |
| `openai` | `.[openai]` | `OPENAI_API_KEY` | `--provider openai -k "$OPENAI_API_KEY" --model gpt-4o` |
| `atlascloud` | `.[atlascloud]` | `ATLASCLOUD_API_KEY` or `ATLAS_CLOUD_API_KEY` | `--provider atlascloud -k "$ATLASCLOUD_API_KEY" --model qwen/qwen3.5-flash` |
| `openrouter` | `.[openrouter]` | `OPENROUTER_API_KEY` | `--provider openrouter -k "$OPENROUTER_API_KEY" --model openai/gpt-4o` plus explicit judge |
| `gemini` | `.[gemini]` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `--provider gemini -k "$GEMINI_API_KEY"` |
| `azure` | `.[azure]` | `AZURE_OPENAI_API_KEY` | `--provider azure --endpoint https://YOUR_RESOURCE.openai.azure.com/ -k "$AZURE_OPENAI_API_KEY" --model YOUR_DEPLOYMENT_NAME` plus explicit judge |
| `bedrock` | `.[bedrock]` | AWS credential chain | `--provider bedrock -k not-used --model anthropic.claude-sonnet-4-6` |
| `huggingface` | `.[huggingface]` | `HF_TOKEN` or `HUGGINGFACE_API_TOKEN` | `--provider huggingface -k "$HF_TOKEN" --model meta-llama/Llama-3.1-8B-Instruct` |
| `langchain` | none (`pip install langchain` on your server only) | key for the model behind your server | `--provider langchain --endpoint http://localhost:8000 -k "$OPENAI_API_KEY" --eval-mode self` |
| `mock` | none | none | `--provider mock` |

Notes:

- `.[all]` installs every SDK above; `.[dev]` is for [contributing](../CONTRIBUTING.md) only.
- `http` tests your deployed agent as-shipped; `--grounding sut` grades the governance it already enforces.
- `bedrock` uses the standard AWS credential chain; `--api-key` is a required placeholder, never sent.
- `litellm` extra is [Python API](python-api.md) only (`provider="litellm"`), not a CLI choice.
- An `openrouter` judge falls back to other models when the gateway errors, so a 502 costs one probe, not the run. Chain: [`ifixai/judge/judge_fallbacks.json`](../ifixai/judge/judge_fallbacks.json), see [cli.md](cli.md#judge-fallback-models).

## LangServe wire contract

`--provider langchain` is a thin HTTP client for a [LangServe](https://python.langchain.com/docs/langserve/)
endpoint; iFixAi never imports LangChain. Serve any Runnable that accepts
`{"messages": [...]}` via `add_routes(app, agent_runnable)`. iFixAi POSTs to
`{endpoint}/invoke` (default endpoint `http://localhost:8000`):

```jsonc
// request
{ "input":  { "messages": [ { "role": "user", "content": "…" } ] },
  "config": { "configurable": { "temperature": 0.0 } } }   // seed / max_tokens added when set

// response: `output` as a string, or `output.content` if it is an object
{ "output": "the agent's reply" }
```

Source: [`ifixai/providers/langchain.py`](../ifixai/providers/langchain.py). To expose
structural surfaces (tools, audit trail, authorization), use a `ChatProvider` adapter (Path 2).
