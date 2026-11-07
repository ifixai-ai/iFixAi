# Business Policy Lab

A tool can be permitted in isolation while its combination with other permitted tools violates a business invariant. Policy Lab explores an explicit finite business model, retains the shortest harmful sequence for each invariant, and searches permission changes that preserve legitimate completion goals.

This is a separate offline assurance surface. It does not change inspection grades or execute providers. Its input is a declared business transition model, not a tool description or a recorded conversation.

## Run the local refund example

From a repository checkout with iFixAi installed:

```sh
python examples/business_policy_lab.py --output ./refund-policy-evidence
```

The example uses actual supplied Python callables and an independent observer of a benign local accounting object. Three individually permitted operations request, self-approve and pay a synthetic refund. Policy Lab discovers the three-step counterexample. Removing the agent's approval permission eliminates the modeled harm while the reviewer-approved payment goal remains reachable. Both the unsafe witness and the repaired legitimate witness are replayed against the local object. No real money or remote services run.

The output contains model and policy JSON, original and selected bundles, repair evidence, observed callable execution reports, and HTML/Markdown reports for check, replay, repair and comparison.

## Declare business behavior

Use `BusinessModel`, `ExplorationPolicy` and `SearchConfig` from `ifixai.policy_lab` or the main operations from `ifixai.api`. See the executable example for a complete model.

- `initial_facts` declares 1–64 exact Boolean facts. Every guard, effect, forbidden condition and goal references a declared fact. Missing facts are errors.
- `actors` maps exact IDs to explicit roles. Actions specify one actor, exact tool, resource, Boolean guards, simultaneous effects and optional integer cost units.
- `properties` names forbidden Boolean conjunctions. A violated initial state produces a zero-step counterexample.
- `goals` names required legitimate completion conjunctions. These are existential reachable outcomes, not universal liveness guarantees.
- Policy permission rules match an actor role, exact tool and resource. Resource scopes are exact strings or one terminal `*` prefix wildcard. Deny is the default.
- Optional trace or actor budgets charge declared cost units. Repeated constraints on one pool apply their strictest limit. Budget counters are part of state identity, even when facts return to an earlier value.
- History, ownership, cross-tenant behavior and other business semantics must be encoded explicitly in facts and action contracts. A tenant ID must match between the model and policy; it does not authenticate a deployment.

All models are bounded, typed and reject extra fields, coercions and ambiguous IDs. No action implementation is inferred from descriptions or imported by name.

## Search, replay, compare and repair

```sh
ifixai policy-lab check model.json policy.json --output ./new-check
ifixai policy-lab replay new-check/bundle.json --output ./new-replay
ifixai policy-lab compare new-check/bundle.json candidate-policy.json --output ./new-comparison
ifixai policy-lab repair new-check/bundle.json --max-removals 1 --output ./new-repair
```

Each destination must be new; evidence files are created with owner-only permissions. HTML reports are self-contained and include searchable witness paths, permission matches, before/after facts, budgets, goals, frontier coverage and cutoffs. Comparison retains the exact model and search configuration and shows property and goal status changes.

Breadth-first search uses deterministic action-ID order and exact facts plus configured budget counters. It retains parent pointers to reconstruct shortest witnesses rather than copying all paths. Default limits are depth 12, 10,000 states and 200,000 state/action examinations. Check supports depth up to 64, 100,000 states and 2,000,000 examinations. Limits constrain exploration; they are not wall-clock or memory guarantees.

| Decision | Meaning | Exit |
| --- | --- | --- |
| `COUNTEREXAMPLE` | At least one reachable forbidden condition has a retained witness, including if exploration later stops. | 1 |
| `VERIFIED` | Complete declared frontier, all properties safe, all goals reachable, at least one enabled transition. | 0 |
| `GOAL_BLOCKED` | Complete declared frontier but at least one completion goal unreachable. | 1 |
| `INCONCLUSIVE` | Limits or missing useful transition coverage prevent a global verification. | 2 |

Per-property verification can be conclusive in a complete zero-edge graph, but global `VERIFIED` is disallowed. Reaching a depth bound does not truncate a graph if every enabled edge returns to an already visited state. Disabled actions do not create edges. Reports count all state/action examinations and identify actions never observed enabled.

Bundles include the complete inputs, limits, report and canonical SHA-256 integrity digest. Replay strictly parses and revalidates the schema, verifies the digest and recomputes every report field. Re-signing a false report still fails. Digests establish consistency, not signer identity or real-world truth.

Repair searches removal of one or two permission rules, in ascending cardinality and rule-index order. It accepts only fully `VERIFIED` candidates, including goal preservation. Denying all operations is not an accepted repair. The default 64-candidate and 250,000 shared transition-examination budgets bound the batch; caps are 256 candidates and 2,000,000 examinations. A smaller inconclusive candidate prevents a minimum-cardinality claim. `EXHAUSTED` refers only to the requested removal space; `FOUND` does not modify deployed permissions. Repair exits 0 for `FOUND`, 1 for `EXHAUSTED`, 2 for `INCONCLUSIVE`.

Repair report export independently reruns that bounded search before creating the destination. It rejects changed summaries, unrelated baselines or selected bundles, and inconsistent minimum-cardinality claims. This validation adds another local search; it does not execute action implementations or providers.

## Bridge a fixture's authorization matrix

```sh
ifixai policy-lab from-fixture model.json customer_support --output ./new-fixture-check
```

`policy_from_fixture(fixture, model)` projects exact user IDs, role memberships and declared tool permissions. Wildcard tools expand to known fixture tools. Unknown IDs, mismatching model actors, duplicate authorization rows and contradictory governance/top-level matrices are rejected. Fixtures do not define resource scopes or business effects: projected scopes are `*`, the caller supplies the tenant and transition contracts, and metadata records that boundary. Tool prose and mock fixture responses are not executed business behavior.

## Observe a local implementation

```python
from ifixai.policy_lab import execute_witness

execution = execute_witness(
    bundle, bundle.report.safety_witnesses[0],
    bindings={"action_id": actual_callable},
    reset=reset_local_state,
    observe_state=read_all_declared_boolean_facts,
)
```

Supply bindings for every action in the retained witness. Each synchronous callable receives `ExecutionContext` with actor, action, tool, resource and declared cost. Retained evidence and all binding declarations validate before reset or calls. An observer supplies the full exact fact snapshot before and after every action; action return values do not establish state. A mismatch stops before the next action. Missing bindings, invalid evidence and step limits are `INCONCLUSIVE`; application exceptions or deferred returns produce `EXECUTION_ERROR`. Errors retain bounded sanitized type names, never exception payloads.

`MATCHED` means this local implementation matched this particular witness. It is not full runtime verification, enforcement, isolation, a timeout, or production authorization. Supplied callables can have side effects before raising. Costs are declared units, not observed billing. Run such replay only against an implementation and reset you explicitly intend to exercise.

## What it doesn't do

- Results hold only for the model you declare. Leave out state the business depends on and the search can't see it.
- `VERIFIED` isn't proof that the deployment is safe or fair, that goals are reached on every path, or that one tenant can't act on another's data.
- Search grows exponentially with facts and budget counters. Hard limits stop it, and a search that stops early is never `VERIFIED`.
- Replaying a witness on your code checks that one path on that one local setup. It can miss side effects the model doesn't describe.
- Callables you pass in run with whatever access they already have.
- The smallest repair means the fewest rule removals within the search you asked for. It doesn't mean it's the best policy for your business.

## Validate changes

```sh
python -m pytest ifixai/tests/policy_lab -q
```

The suite includes an independent exhaustive primitive oracle, budget-sensitive state identity, limit/fixed-point behavior, malformed and re-signed evidence, goal-preserving repairs, fixture/CLI integration, escaped reports and observed native-callable mismatches. Synthetic distinct-state benchmarks are local resource measurements, not production latency claims.
