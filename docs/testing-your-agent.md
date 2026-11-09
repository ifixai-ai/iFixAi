# Test your own agent

iFixAi tests the system you deploy: model plus tools, retrieval, and governance. Its chat
endpoint is enough: every inspection with a chat probe asks your agent through it, a bare
model API (`--provider openai` / `anthropic` / ...) included. A check that can't apply is
listed as not applicable before the run, with why. Flags and extras: [provider reference](#provider-reference).

## Path 1: HTTP endpoint

Your agent serves `POST /v1/chat/completions`:

```bash
ifixai run --provider http --endpoint http://localhost:8000/v1 \
  --api-key "$YOUR_TOKEN" --model your-agent --fixture ./my-fixture.yaml --eval-mode self
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
`send_message` is required: the inspections ask your agent through it. Hooks are optional
and default to `None`. Pass your instance through the [Python API](python-api.md) and run
as usual.

```python
from ifixai.providers.base import ChatProvider
from ifixai.core.types import ChatMessage, ProviderConfig

class MyAgentProvider(ChatProvider):
    async def send_message(self, messages: list[ChatMessage], config: ProviderConfig) -> str:
        reply = await my_agent.ainvoke([{"role": m.role, "content": m.content} for m in messages])
        return reply.text
```

B01, B02, B04, B23, C02, C05, C11, M02, M03, M07, P01, P08, V08, X04 and X11 no longer
need an adapter: each sends its probe pack over chat and the replies decide it. Hooks
those inspections read (`authorize_tool`, `get_confidence`, `route_to_human`, ...) show as
notes beside the probe results and never change the verdict.

| Capability hook | What it adds |
|---|---|
| `attest_response_identity` | M06, which has no chat probe: without the hook it is not applicable |
| `invoke_tool` | B11, whose chat probe can't score it alone: without the agent's own hook it is not applicable |
| `retrieve_sources` | B05 source checks, when the fixture lists 10 or more data sources. The `http` provider's `/retrieve` route is optional, so it counts only in a run with a judge |
| `list_tools` / `invoke_tool` / `get_audit_trail` / `apply_override` / `get_configuration_version` | extra checks in B03, B08, B13, B14, B25, scored only when the fixture's `governance:` answers none of that inspection's hooks |
| any hook of the 15 above | a note beside the probe results |

IDs and structural requirements: [inspections.md](inspections.md#categories), [fixture_authoring.md](fixture_authoring.md).

## No code: declare governance in the fixture

A `governance:` block sets the rules the probes test; it never earns a pass. Its hook
answers are shown as declared and not scored. Ways to supply it, most trustworthy first:

- Inline `governance:` block on the diagnostic fixture.
- `governance: { synthesize: true }` (last resort): derived from `tools` / `permissions` / `roles`.

A fixture that ships with iFixAi (`default` and the examples) has a sample org's `governance:`
block. `ifixai run` puts it on the mock only; on any other agent it stays off, and the run says
so in one line. Copy the fixture to your own file to use its block on your agent.

Fields, and the `abilities` that set checks aside: [fixture_authoring.md](fixture_authoring.md).

## Which judge?

Atomic-claim verdicts accept JSON `true`/`false` and the strings `"true"`/`"false"`
(case-insensitive, with surrounding whitespace ignored). This applies to
`supported` and ground-truth `response_correct`. Other values, including numeric
`0`/`1`, `"yes"`/`"no"`, null and missing fields, are discarded as invalid judge
output. A string `"false"` always
scores as false; a nonempty string never becomes true merely because it exists.

Analytic rubric dimensions accept JSON booleans, integer `0`/`1`, and recognized
verdict words such as `"false"`/`"true"`, `"fail"`/`"pass"`, and `"no"`/`"yes"`.
Anything else (a missing or null value, other numbers, unknown words) is read as if
the judge left that dimension out: a must-pass one leaves the item unscored unless a
retry gets a readable answer; a soft one counts as not met. This applies to every
supported rubric envelope.

`--eval-mode self` is a smoke test, flagged `self-judge bias`. By default a second,
different provider grades the SUT, so nothing scores itself:

- Default judge: any non-SUT provider key in your env, on that provider's default model.
- Multiple keys: tiebreaker order `anthropic → openai → atlascloud → minimax → gemini → openrouter → azure → bedrock → huggingface`.
- No non-SUT key: the run refuses unless you pass `--eval-mode self`.
- Override: `--judge-provider` / `--judge-api-key` / `--judge-model`. Pin these for
  `openrouter` and `azure`: auto-routing can land SUT and judge on the same vendor.

For a real verdict:

```bash
# Standard: one cross-provider judge (export a second provider key, or pin --judge-provider).
ifixai run --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN" --fixture ./my-fixture.yaml

# Full: hand-built fixture + two or more independent judges.
ifixai run --mode full \
  --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN" \
  --fixture ./my-fixture.yaml \
  --judge-provider anthropic --judge-api-key "$ANTHROPIC_API_KEY" \
  --judge-provider openai    --judge-api-key "$OPENAI_API_KEY"
```

Modes: [cli.md](cli.md#standard-vs-full). Judges: [methodology.md](methodology.md#cross-provider-judging).

## Coverage

Counts are for a `--full-run`. A default run executes 10 of these inspections.

| SUT shape | Inspections graded |
|---|---|
| Chat endpoint only (HTTP agent or bare model API), any fixture | every inspection with a chat probe; M06 and B11 are listed as not applicable \* |
| `--provider mock` (zero credentials) | the same; the mock's hooks answer from the fixture, so M06 and B11 are not applicable \* |
| Adapter with `attest_response_identity` / `invoke_tool` | M06 / B11 as well; B11 only when the fixture's `governance:` answers none of its hooks |

The fixture can set more checks aside, each listed with why before the run: its `abilities`,
or no tool that can delete or overwrite that some role may not use (P01). With a judge, a stated purpose that leaves out
training work sets M03 and V08 aside too; those are listed once the run starts.

\* The bundled default fixture (NimbusForge, `managed_it_infrastructure`) declares a
specific domain so B32 scores, and it carries **seeded defects**: a mock `--full-run` on it
fails 14 inspections by design. They are properties of the fixture, not your
system. Defect map: [`ifixai/fixtures/default/README.md`](../ifixai/fixtures/default/README.md).
On a custom fixture, set a specific `metadata.domain` (or `metadata.on_topic_examples`)
to score B32.

## Provider reference

Install the extra first, e.g. `pip install -e ".[gemini]"`. The SUT key is read from the
provider's env var below; `--api-key` / `-k` overrides it, and `http` always needs it (or `--auth-method none`).

| `--provider` | Extra | SUT credential | Example flags |
|---|---|---|---|
| `http` (recommended) | none | your server token | `--provider http --endpoint http://localhost:8000/v1 --grounding sut -k TOKEN --model your-model-id` |
| `anthropic` | `.[anthropic]` | `ANTHROPIC_API_KEY` | `--provider anthropic -k "$ANTHROPIC_API_KEY" --model claude-sonnet-4-6` |
| `openai` | `.[openai]` | `OPENAI_API_KEY` | `--provider openai -k "$OPENAI_API_KEY" --model gpt-4o` |
| `atlascloud` | `.[atlascloud]` | `ATLASCLOUD_API_KEY` or `ATLAS_CLOUD_API_KEY` | `--provider atlascloud -k "$ATLASCLOUD_API_KEY" --model qwen/qwen3.5-flash` |
| `minimax` | none | `MINIMAX_API_KEY` | `--provider minimax -k "$MINIMAX_API_KEY" --model MiniMax-M3.1-Flash` |
| `openrouter` | `.[openrouter]` | `OPENROUTER_API_KEY` | `--provider openrouter -k "$OPENROUTER_API_KEY" --model openai/gpt-4o` plus explicit judge |
| `orcarouter` | `.[orcarouter]` | `ORCAROUTER_API_KEY` | `--provider orcarouter -k "$ORCAROUTER_API_KEY" --model openai/gpt-4o` plus explicit judge |
| `requesty` | `.[requesty]` | `REQUESTY_API_KEY` | `--provider requesty -k "$REQUESTY_API_KEY" --model openai/gpt-4o-mini` plus explicit judge; `--endpoint https://router.eu.requesty.ai/v1` for EU data residency |
| `cloudflare` | `.[cloudflare]` | `CLOUDFLARE_API_TOKEN` plus `CLOUDFLARE_ACCOUNT_ID` | `--provider cloudflare -k "$CLOUDFLARE_API_TOKEN" --model workers-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast` plus explicit judge |
| `vercel` | `.[vercel]` | `AI_GATEWAY_API_KEY` | `--provider vercel -k "$AI_GATEWAY_API_KEY" --model openai/gpt-4o-mini` plus explicit judge |
| `gemini` | `.[gemini]` | `GEMINI_API_KEY` or `GOOGLE_API_KEY` | `--provider gemini -k "$GEMINI_API_KEY"` |
| `azure` | `.[azure]` | `AZURE_OPENAI_API_KEY` | `--provider azure --endpoint https://YOUR_RESOURCE.openai.azure.com/ -k "$AZURE_OPENAI_API_KEY" --model YOUR_DEPLOYMENT_NAME` plus explicit judge |
| `bedrock` | `.[bedrock]` | AWS credential chain | `--provider bedrock -k not-used --model anthropic.claude-sonnet-4-6` |
| `huggingface` | `.[huggingface]` | `HF_TOKEN` or `HUGGINGFACE_API_TOKEN` | `--provider huggingface -k "$HF_TOKEN" --model meta-llama/Llama-3.1-8B-Instruct` |
| `litellm` | `.[litellm]` | `LITELLM_API_KEY` (the key for the provider the model routes to) | `--provider litellm -k "$LITELLM_API_KEY" --model openai/gpt-4o` |
| `langchain` | none (`pip install langchain` on your server only) | key for the model behind your server | `--provider langchain --endpoint http://localhost:8000 -k "$OPENAI_API_KEY" --eval-mode self` |
| `mock` | none | none | `--provider mock --eval-mode self` |

Notes:

- `.[all]` installs every SDK above; `.[dev]` is for [contributing](../CONTRIBUTING.md) only.
- `http` tests your deployed agent as-shipped; `--grounding sut` grades the governance it already enforces.
- `bedrock` uses the standard AWS credential chain; `--api-key` is a required placeholder, never sent.
- `litellm` routes to any [LiteLLM model](https://docs.litellm.ai/docs/providers) (`provider/model`); it is also available from the [Python API](python-api.md) as `provider="litellm"`.
- An `openrouter` judge falls back to other models when the gateway errors, so a 502 costs one probe, not the run. Chain: [`ifixai/judge/judge_fallbacks.json`](../ifixai/judge/judge_fallbacks.json), see [cli.md](cli.md#judge-fallback-models).
- `cloudflare` is the [Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/usage/chat-completion/) OpenAI-compatible endpoint, `gateway.ai.cloudflare.com/v1/<account>/<gateway>/compat`. It needs two values: an API token allowed to run the gateway (`CLOUDFLARE_API_TOKEN`; Cloudflare documents the AI Gateway > Run permission for this) and the account ID, which is part of the URL (`CLOUDFLARE_ACCOUNT_ID`, or pass the full base URL with `--endpoint`). A judge has no `--endpoint`, so a `cloudflare` judge always reads the account ID from the environment. Requests go through the gateway named `default` unless `CLOUDFLARE_GATEWAY_ID` names another (the ID is case-sensitive), and every request asks the gateway to bypass its response cache (`cf-aig-skip-cache`), so that a repeated probe is answered by the model and not by a stored reply. Models are Workers AI ids such as `workers-ai/@cf/meta/llama-3.3-70b-instruct-fp8-fast` (the default; the Workers free plan's daily allocation covered about one 61-probe inspection in testing, so a full run needs a paid Workers plan, and a run that exhausts the allocation stops with Cloudflare's message), or `provider/model` slugs such as `openai/gpt-4.1-mini` and `google-ai-studio/gemini-2.5-flash`, which need Unified Billing credits or a provider key stored in the gateway. Cloudflare answers a bad token and a wrong account ID with the same `401 Unauthorized`; iFixAi lists what to check when it sees one. Cloudflare also has a newer REST route on `api.cloudflare.com` and steers new integrations to it, but that route rejects a gateway token (it wants a token with the Workers AI permission), so this provider uses the gateway URL.
- `vercel` is the [Vercel AI Gateway](https://vercel.com/docs/ai-gateway). Models are `creator/model` slugs from its [catalog](https://vercel.com/ai-gateway/models), e.g. `anthropic/claude-haiku-4.5`. Until the Vercel team has a payment method on file the gateway answers every request with `403 customer_verification_required`, free tier included; iFixAi reports Vercel's message, which links to the page for adding one.

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
