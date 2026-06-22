# Test your own agent

iFixAi inspects the AI system **you actually deploy**: your *agent*, not just the
model underneath it. This page explains the model-vs-agent distinction, then shows
the three ways to point a run at your stack and how each one affects coverage.

## Model vs agent

iFixAi treats the system under test (SUT) as a black box behind a single seam: a
[`ChatProvider`](../ifixai/providers/base.py) that takes chat messages and returns a
reply. *What sits behind that seam* determines how much of the suite is measurable.

- A **bare model** (`--provider openai`, `anthropic`, `gemini`, …) answers chat and
  exposes nothing else. The structural inspections (tool authorization, audit
  trail, deterministic override, policy-version traceability, RAG retrieval) have
  no surface to call, so they return `insufficient_evidence` and drop out of
  aggregation. A vanilla LLM therefore scores **34 of 45**.
- An **agent** is that model *plus* its system prompt, tools, retrieval, guardrails,
  and governance layer, the thing your business runs. When your adapter exposes
  those surfaces, the structural inspections become measurable and a run can reach
  the full **45 of 45**.

This is not iFixAi being lenient on models and harsh on agents; it refuses to
invent a score where there is no surface to measure. The number that matters is the
one for *your deployment*, so point it at your agent.

| What you point at | How | Inspections scored |
|---|---|---|
| Bare model API | `--provider openai` / `anthropic` / … | 34 / 45 (28 core + 6 extended) |
| OpenAI-compatible agent service | `--provider http --endpoint …` | behavioural suite, **+ RAG (B28)** when the service returns `sources` |
| Custom `ChatProvider` with capability hooks | subclass + implement hooks | up to **45 / 45** |
| Any fixture with a `governance:` block | `--governance` / inline | structural cluster scored from the *declared* policy (flagged in `warnings[]`) |

The exact per-shape counts and the three `governance:`-block options live in
[Governance and scoring coverage](#governance-and-scoring-coverage).

There are two mechanisms, and both are independent of how your agent is built: point
iFixAi at an **HTTP endpoint** your agent already serves (Path 1), or wrap your
runtime in a small **`ChatProvider` adapter** (Path 2). Ready-made adapters and
per-runtime recipes are catalogued in [docs/providers.md](providers.md).

## Path 1: your agent serves an HTTP endpoint

If your agent already exposes `POST /v1/chat/completions`, point `--provider http`
at it. iFixAi sends the standard chat payload and reads `choices[0].message.content`.

```bash
ifixai run --provider http \
  --endpoint http://localhost:8000/v1 \
  --api-key "$YOUR_TOKEN" \
  --model your-agent \
  --eval-mode self
```

To unlock the **RAG context-integrity** inspection (B28), return a `sources` array
next to `choices` (the adapter also fetches `POST {endpoint}/retrieve` when an
inspection asks for sources directly):

```jsonc
{ "choices": [ { "message": { "role": "assistant", "content": "…" } } ],
  "sources": [ { "document_uri": "kb://policy/42", "text": "…", "relevance_score": 0.91 } ] }
```

Custom auth headers: set `IFIXAI_EXTRA_HEADERS` to a JSON object. The auth scheme
(`bearer` / `api_key` / `basic` / `none`) is selectable via the `auth_method`
config field from the [Python API](python-api.md). Details in
[`ifixai/providers/http.py`](../ifixai/providers/http.py).

## Path 2: wrap your runtime in a `ChatProvider` adapter

For an in-process agent, or to expose the structural surfaces an HTTP chat endpoint
does not carry, subclass [`ChatProvider`](../ifixai/providers/base.py).
`send_message` is the only required method; every capability hook defaults to
`None`, and returning `None` keeps the matching inspection at
`insufficient_evidence`. Implement a hook and that inspection becomes measurable.

```python
from ifixai.providers.base import ChatProvider
from ifixai.core.types import ChatMessage, ProviderConfig

class MyAgentProvider(ChatProvider):
    async def send_message(
        self, messages: list[ChatMessage], config: ProviderConfig
    ) -> str:
        # Hand the rendered turns to your agent and return its reply text.
        reply = await my_agent.ainvoke(
            [{"role": m.role, "content": m.content} for m in messages]
        )
        return reply.text

    # Optional capability hooks: implement the ones your agent actually has.
    # Return types are defined in ifixai/core/types.py; returning None (the
    # default) routes the matching inspection to insufficient_evidence.
    async def authorize_tool(self, tool_id, user_role, config):
        decision = my_agent.policy.check(user_role, tool_id)
        ...   # build and return a ToolInvocationResult
```

Register the provider in the resolver (or run it through the
[Python API](python-api.md) by passing your instance directly) and run as
usual.

### Which hook feeds which inspection

Each capability hook you implement turns on exactly the inspections below — implement none and those checks stay at `insufficient_evidence`; implement a hook and its row becomes measurable.

| Capability hook on `ChatProvider` | Makes measurable |
|---|---|
| `list_tools` / `invoke_tool` / `authorize_tool` | **B01** tool-authorization leak, and the tool-calling-dependent checks |
| `get_governance_architecture` | **B02** non-LLM governance layer |
| `apply_override` | **B04** deterministic override |
| `get_configuration_version` | **B23** policy-version traceability |
| `get_audit_trail` | per-action audit trail |
| `retrieve_sources` | **B28** RAG context integrity & source provenance |
| `get_confidence` | **C02** miscalibration *(exploratory)* |
| `route_to_human` | **C05** human-routing fallback *(exploratory)* |
| `reconcile_outcome` | **C11** outcome reconciliation *(exploratory)* |
| `evaluate_deployment_gate` | **X04** perception governance *(exploratory)* |
| `evaluate_confirmation_gate` | **X11** oversight atrophy *(exploratory)* |

The canonical `B01`-`X11` → category mapping and per-inspection structural
requirements are in
[docs/inspection_categories.md](inspection_categories.md) and
[docs/fixture_authoring.md](fixture_authoring.md).

## No code? Declare governance in the fixture instead

If you cannot expose structural hooks programmatically, the governance inspections
(B02 / B04 / B11 / B23 / B26 / B27) can still be scored from a declared policy:
supply a `governance:` block (inline, via `--governance`, or synthesized) in your
fixture. The scorecard records that governance was scored from the declared fixture
rather than measured at runtime, via a `warnings[]` entry. See
[Governance and scoring coverage](#governance-and-scoring-coverage) and
[docs/fixture_authoring.md](fixture_authoring.md).

## Which judge?

**What you test (the SUT, above) and who grades it (the judge) are independent
choices.** Every example above uses `--eval-mode self` for a one-command start: a
self-judge, flagged in the scorecard with a `self-judge bias` warning. Fine for a
smoke test, not for a real verdict. Keep your agent as the SUT and add a real judge:

```bash
# Standard: one cross-provider judge. Export a second provider key and iFixAi
# auto-pairs it (or pin it with --judge-provider / --judge-api-key).
ifixai run --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN"

# Full: a hand-built fixture and an ensemble of TWO or more independent judges.
ifixai run --mode full \
  --provider http --endpoint http://localhost:8000/v1 --api-key "$YOUR_TOKEN" \
  --fixture ./my-fixture.yaml \
  --judge-provider anthropic --judge-api-key "$ANTHROPIC_API_KEY" \
  --judge-provider openai    --judge-api-key "$OPENAI_API_KEY"
```

The mode controls judge and fixture rigor, not which inspections run; see
[Standard vs Full mode](running.md#standard-vs-full-mode). Judge
selection details: [docs/methodology.md](methodology.md#cross-provider-judge-default).

## Governance and scoring coverage

How many of the 45 inspections get scored depends on two things: what your SUT
exposes, and whether the fixture carries a `governance:` block. Eleven inspections
ride on provider capability hooks — four core (B01, B02, B04, B23) and seven
structural extended (P01, P08, C02, C05, C11, X04, X11).

| SUT shape | Inspections scored |
|---|---|
| Vanilla LLM, **default fixture** (ships a `governance:` block) | 44 of 45 \* |
| Vanilla LLM, **custom fixture without** a governance block | 34 of 45 (28 core + 6 extended) |
| `--provider mock` (zero credentials) | 45 of 45 |
| Policy-wrapped provider, or an agent exposing every hook | 45 of 45 |
| Full mode + multi-judge ensemble | 45 of 45 |

\* with a `warnings[]` entry noting governance was scored from the declared fixture
rather than measured at runtime; B32 off-topic detection additionally needs a fixture
with a specific, non-generic domain.

When you author your own fixture, there are three ways to wire governance (lowest
friction last):

- **`--governance <path>`** — supply an external `GovernanceFixture` YAML; iFixAi wraps the provider with `GovernanceMixin` automatically, no subclassing.
- **An inline `governance:` block** on the diagnostic fixture — one YAML for tests *and* policies.
- **`governance: { synthesize: true }`** — derives a structural policy bundle from your `tools`, `permissions`, and `roles`. Lower friction, less precise: the scorecard records that the bundle was synthesised, not measured.

The per-inspection field requirements (which `governance:` fields B02 / B04 / B11 / B23 /
B26 / B27 read, and what values make them pass) are in
[fixture_authoring.md](fixture_authoring.md); the design discussion and manifest fields
are in [methodology.md](methodology.md).
