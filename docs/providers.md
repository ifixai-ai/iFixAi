# Provider setup recipes

How to point `ifixai run` at each supported provider. The mock and Anthropic
recipes live in the [README quick start](../README.md#quick-start) and
[get-started.md](get-started.md); this page covers the rest.

Install the optional extra for the provider you will call (see
[Provider extras](#provider-extras) below), for example
`pip install -e ".[gemini]"`.

## Provider extras

Extras only pull SDKs; the core CLI deps are always installed.

| Extra | Installs | Use for `--provider` |
|---|---|---|
| *(none)* | Core only | `mock`, `http`, `langchain` (you must `pip install langchain` yourself) |
| `openai` | `openai` SDK | `openai` |
| `anthropic` | `anthropic` SDK | `anthropic` |
| `openrouter` | `openai` SDK (OpenRouter exposes an OpenAI-compatible endpoint) | `openrouter` |
| `gemini` | `google-generativeai` | `gemini` |
| `azure` | `openai` SDK | `azure` (set `--endpoint` to your Azure OpenAI resource) |
| `bedrock` | `boto3` | `bedrock` |
| `huggingface` | `huggingface-hub` | `huggingface` |
| `litellm` | `litellm` SDK | [Python API](python-api.md) only; not a CLI `--provider` choice |
| `all` | Every provider SDK above | Any of the above |
| `dev` | Lint, types, tests, security | [contributing](../CONTRIBUTING.md) only |

## Judge selection

By default, iFixAi grades the SUT with a second, different provider so nothing scores itself — set a non-SUT provider key in your environment, or pass `--eval-mode self`. The CLI expects **a second, different provider credential in the environment** so the system under test (SUT) is not scored by itself:

- **Default:** judge = any non-SUT provider key in your env, run on that provider's default model.
- **Multiple keys:** tiebreaker order is `anthropic → openai → gemini → openrouter → azure → bedrock → huggingface`.
- **No non-SUT key:** pass `--eval-mode self`, or the run refuses. A self-judge is fine for mock/CI drift, not for vendor comparisons.
- **Override:** `--judge-provider` / `--judge-api-key` / `--judge-model`.

The CLI does **not** auto-read the SUT API key from the environment: pass
**`--api-key`** / **`-k`**, or enter it when prompted.

## Anthropic

```bash
pip install -e ".[anthropic]"
export ANTHROPIC_API_KEY=sk-ant-api03-...
export GEMINI_API_KEY=...   # second provider for cross-judge (or use --eval-mode self)
ifixai run --provider anthropic --api-key "$ANTHROPIC_API_KEY" --model claude-sonnet-4-6
```

## OpenRouter (explicit judge)

Pin the judge explicitly for OpenRouter — its routing can send the SUT to the same vendor as the auto-judge (e.g. an Anthropic model judged by Anthropic), collapsing the independence the score depends on.

```bash
pip install -e ".[openrouter]"    # installs openai SDK; OpenRouter is OpenAI-compatible; other compatible SDKs or --provider http work too
export OPENROUTER_API_KEY=sk-or-...
export ANTHROPIC_API_KEY=sk-ant-api03-...
ifixai run --provider openrouter --api-key "$OPENROUTER_API_KEY" --model openai/gpt-4o \
  --judge-provider anthropic --judge-api-key "$ANTHROPIC_API_KEY" --judge-model claude-sonnet-4-6
```

## Google Gemini

```bash
pip install -e ".[gemini]"
export GEMINI_API_KEY=...    # or GOOGLE_API_KEY
export ANTHROPIC_API_KEY=sk-ant-api03-...   # second provider for cross-judge (or use --eval-mode self)
ifixai run --provider gemini --api-key "$GEMINI_API_KEY"
```

## Azure OpenAI (explicit judge)

```bash
pip install -e ".[azure]"          # or .[openai], same OpenAI-compatible SDK
export AZURE_OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=sk-ant-api03-...
ifixai run --provider azure \
  --endpoint https://YOUR_RESOURCE.openai.azure.com/ \
  --api-key "$AZURE_OPENAI_API_KEY" \
  --model YOUR_DEPLOYMENT_NAME \
  --judge-provider anthropic --judge-api-key "$ANTHROPIC_API_KEY" --judge-model claude-sonnet-4-6
```

## AWS Bedrock

Bedrock authenticates through the standard AWS credential chain (env vars or instance profile), so `--api-key` is a required placeholder that is never sent — use any string.

```bash
pip install -e ".[bedrock]"
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export GEMINI_API_KEY=...   # second provider for cross-judge (or use --eval-mode self)
ifixai run --provider bedrock --api-key not-used \
  --model anthropic.claude-sonnet-4-6
```

## Hugging Face Inference

```bash
pip install -e ".[huggingface]"
export HF_TOKEN=hf_...
export ANTHROPIC_API_KEY=sk-ant-api03-...   # second provider for cross-judge (or use --eval-mode self)
ifixai run --provider huggingface --api-key "$HF_TOKEN" --model meta-llama/Llama-3.1-8B-Instruct
```

(`HUGGINGFACE_API_TOKEN` is also accepted.)

## HTTP (OpenAI-compatible server)

```bash
pip install -e "."
export GEMINI_API_KEY=...   # second provider for cross-judge (or use --eval-mode self)
ifixai run --provider http \
  --endpoint http://localhost:8000/v1 \
  --api-key YOUR_SERVER_TOKEN \
  --model your-model-id
```

Optional JSON headers: set **`IFIXAI_EXTRA_HEADERS`** to a JSON object (see [`ifixai/providers/http.py`](../ifixai/providers/http.py)).

## LangChain / LangServe

The `langchain` provider is a thin **HTTP client** for a
[LangServe](https://python.langchain.com/docs/langserve/) endpoint. iFixAi does
**not** import LangChain itself, so the only thing that needs `langchain` /
`langserve` installed is *your* server, not the iFixAi side. Expose your chain or
agent and iFixAi will drive it:

```python
# your_server.py: the agent under test
from fastapi import FastAPI
from langserve import add_routes
from your_app import agent_runnable        # any Runnable taking {"messages": [...]}

app = FastAPI()
add_routes(app, agent_runnable)            # serves POST /invoke
# run: uvicorn your_server:app --port 8000
```

```bash
pip install -e "."
export OPENAI_API_KEY=sk-...               # one key only; SUT and judge share the same model
ifixai run --provider langchain --endpoint http://localhost:8000 \
  --api-key "$OPENAI_API_KEY" --eval-mode self
```

**Wire contract.** iFixAi POSTs to `{endpoint}/invoke` (default endpoint
`http://localhost:8000`):

```jsonc
// request body iFixAi sends
{ "input":  { "messages": [ { "role": "user", "content": "…" } ] },
  "config": { "configurable": { "temperature": 0.0 } } }   // seed / max_tokens added when set

// response iFixAi reads: `output` as a string, or `output.content` if it is an object
{ "output": "the agent's reply" }
```

So your runnable must accept `{"messages": [...]}` as its input. A bare chat model
returns a message object (iFixAi reads `output.content`); an `AgentExecutor`-style
runnable should return its reply as a string. Source:
[`ifixai/providers/langchain.py`](../ifixai/providers/langchain.py).

For a cross-provider judge instead of `--eval-mode self`, add a second credential
and `--judge-provider` (see [Judge selection](#judge-selection)). To expose
structural surfaces (tools, audit trail, authorization) and lift coverage toward
45/45, write a custom adapter instead; see
[docs/testing-your-agent.md](testing-your-agent.md).

## LiteLLM (Python API only)

The `litellm` extra installs the LiteLLM SDK and registers a
[`LiteLLMProvider`](../ifixai/providers/litellm.py) in the provider resolver,
but `litellm` is not currently a `--provider` choice on the CLI. Use it through
the [Python API](python-api.md) (`provider="litellm"`).
