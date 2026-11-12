# Trace intake: auditing the traces you already collect

`ifixai traces import` turns a trace export from your observability tool into an iFixAi
trajectory capture, the JSONL file a trajectory audit reads: one header row, then one event per
row.

```bash
ifixai traces import export.json --from otel --tenant acme --collector otel-gateway -o capture.jsonl
```

- `--from`: `otel`, `langfuse`, `langsmith`, `arize`, `braintrust`, or `auto` (the default),
  which recognizes the format from the file's structure.
- `--tenant` and `--collector`: written into the capture. They always come from you, never
  from the trace.
- `--trace-id`: picks one trace when the file holds several. Without it, a file with several
  traces is refused.
- `--max-text` (default 200, at most 1000): how many characters of a generation's text are
  kept as the output's label. `0` keeps none.
- `-o`: the capture to write. Import warnings go next to it in `<output>.warnings.json`.
  Neither file is ever overwritten.

From Python:

```python
from ifixai.trace_intake import import_trace, write_result

result = import_trace("export.json", source="auto", tenant_id="acme", collector_id="otel-gateway")
result.capture_rows   # header row, then event rows
result.warnings       # every gap the import found
write_result(result, "capture.jsonl")
```

## What each importer reads

Every capture starts with one `start` event and finishes with one `end` event. In between:

| Source | Becomes an `action` | Becomes an `output` | Becomes a `source` | Read for structure only |
|---|---|---|---|---|
| OpenTelemetry (OTLP JSON, GenAI conventions) | `gen_ai.operation.name = execute_tool` | `chat`, `text_completion`, `generate_content` with final text | `retrieval` | `invoke_agent`, `create_agent`, `embeddings` |
| Langfuse (trace with observations) | `TOOL` observations | `GENERATION` with final text | `RETRIEVER` | `AGENT`, `CHAIN`, `SPAN`, `EMBEDDING`, `EVALUATOR`, `GUARDRAIL` |
| LangSmith (runs, flat or nested) | `run_type = tool` | `llm` with final text | `retriever` | `chain`, `prompt`, `parser`, `embedding` |
| Arize Phoenix / OpenInference | `openinference.span.kind = TOOL` | `LLM` with final text | `RETRIEVER` | `AGENT`, `CHAIN`, `EMBEDDING`, `RERANKER`, `GUARDRAIL`, `EVALUATOR`, `PROMPT` |
| Braintrust (span rows) | `span_attributes.type = tool` | `llm` with final text | (none) | `task`, `function`, `eval`, `score`, `automation` |

What goes into each event:

- **action**: the tool name as `action`, `tool:<name>` as the resource, and a SHA-256 digest of
  the tool's arguments (never the arguments themselves). The outcome comes only from what the
  trace records: an error status or error field gives `failed`, an explicit success status gives
  `completed`, and anything else gives `unknown`. Langfuse and Braintrust do not record success,
  so their tools are `failed` or `unknown`.
- **source**: one event per retrieved document, with the document's id as the resource and a
  digest of its content.
- **output**: the generation's final text, cut to `--max-text` characters. A generation that
  only asks for a tool call is not an output.
- **actor**: the agent the trace names (the agent span or observation, `gen_ai.agent.id`, or an
  explicit `ifixai.actor_id` field). If the trace names none, the placeholder
  `unattributed-agent` is used and a warning is recorded.
- Order and parents follow the trace's own end times and parent links. An event that does not
  come after its traced parent hangs off the start event instead.

The importer only reads the file. Nothing in it is run, opened or fetched. Inputs over 64 MiB,
or with more than 100,000 events, are refused, never cut short.

## What it never infers

- **No approvals, revocations or delegations from names or text.** A span called
  `approve_refund`, a tool called `approve_refund`, or a reply saying "approved by a manager"
  all stay what they are: a structural span, an action, an output. An approval, revocation or
  delegation appears only when the trace carries an explicit `ifixai.event` attribute (OTel,
  OpenInference) or metadata key (Langfuse, LangSmith, Braintrust) holding a canonical event,
  tied to that span's id, trace, tenant and time.
- **No tool calls from untyped spans.** A plain Langfuse `SPAN` or an OTel span without
  `gen_ai.operation.name` is not treated as a tool call, whatever its name.
- **No citations.** Traces do not say which documents an answer used, so outputs carry no
  citations.
- **No completeness.** A capture is marked complete only when the trace says so itself
  (`ifixai.capture_complete = true` on the OTel resource or the root's metadata) *and* the
  import found no gaps.

## Warnings

Every gap is recorded as a warning: dropped attributes, unmapped or unknown span kinds, missing
timestamps, parents missing from the export, missing tool arguments, unknown outcomes, ids
replaced by digests, and content the source had already cut short or redacted. Any warning
leaves the capture incomplete, so an audit of an imported trace can never come back clear on
partial evidence.
