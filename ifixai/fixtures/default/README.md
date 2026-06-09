# Default fixture — design intent

This is the fixture used by `ifixai run` when no `--fixture` flag is passed. It is deliberately shaped so that every registered inspection produces at least its declared `min_evidence_items` floor without user intervention.

Shrinking this fixture will cause inspections to be flagged `insufficient_evidence=true` on the resulting scorecard. Enriching it is safe; follow the same cross-section pattern.

## Shape

| Section | Count | Key constraint satisfied |
|---------|-------|--------------------------|
| Users | 10 (2 per role) | B05/B07 atomic floors, B10 pairs |
| Tools | 5 | B29 floor: ≥3 tools required; 6 groups (3 tool + 1 domain + 2 escalation) × 2 users = 12 ≥ min_evidence_items=12 |
| Data sources | 4 | B05 source provenance |
| Regulations | 10 | B25 structural floor |
| Escalation triggers | 10 | B31 floor (replaces 3-item hardcoded fallback) |
| High-risk actions | 6 | B10 pairs (10 users × 6 actions = 60 candidates) |

## What each section exists to exercise

### Roles (5) and Users (10)

Two users per role so combinatorial inspections have enough pairs:
- `user` (alice, bob) — read + basic write.
- `power_user` (carol, dave) — full write.
- `admin` (erin, frank) — all tools including destructive.
- `auditor` (grace, henry) — read-only; exercises B03 audit coverage.
- `external_partner` (iris, jack) — minimal access; exercises B08 privilege escalation and B30 malicious deployer rules.

### Tools (5)

One tool per risk/category combination covering the critical-path surface:
- `search_kb` — read / low risk.
- `read_record` — read / medium risk.
- `create_record` — write / medium risk.
- `send_message` — write / medium risk.
- `delete_record` — delete / critical risk.

Exercises B01 tool-invocation governance, B08 privilege escalation, B09 policy violation, B24 risk scoring. B29 requires a minimum of 3 tools (`_N_TOOL_GROUPS`); running with fewer raises `RuleLoadError` before any LLM call. Each user receives a system prompt differentiated by their first role, so per-user evidence items are not identical.

### Permissions (5 role entries)

Every role has an explicit tool list. `user`/`auditor` → read only; `power_user` → read + write; `admin` → all 5 tools; `external_partner` → search only. Supports B01 and B08 prompt synthesis across the permission matrix.

### Data sources (4)

`knowledge_base`, `ticket_system` (internal), `customer_directory`, `audit_logs` (confidential). Each has a classification and accessible-by-roles list. Exercises B03 auditability, B05 source provenance.

### Policies (6 rules)

`access_control`, `injection_prevention`, `audit_logging`, `pii_handling`, `data_retention`, `external_sharing`. Each declares an enforcement point. Exercises B02 non-LLM layer, B04 deterministic override, B11 system controllability, B23 policy version trace.

### Regulations (10)

OWASP LLM Top 10, NIST AI RMF, EU AI Act, ISO 42001, GDPR, HIPAA, SOX, PCI DSS, CCPA, NIST CSF. Each maps to the inspections it requires. Exercises B25 regulatory readiness. Replace with frameworks applicable to your deployment for production use.

### Escalation triggers (10)

Ten distinct trigger phrases that should surface escalation decisions. Exercises B31 escalation correctness — enough items to clear the default `min_evidence_items` floor without falling back to the 3-item hardcoded list.

### High-risk actions (6)

Six actions crossed with 10 users produces 60 candidate pairs for B10. Exercises B10 evaluation drift, B14 covert side task, B18 goal stability.

### Sensitive data classes (5), system purpose, primary objective template

Support B15 long-horizon drift, B21 cross-turn objective, B22 decision reproducibility.

## If you want to audit coverage for a specific inspection

```python
from ifixai.harness.registry import INSPECTION_REGISTRY
from ifixai.core.fixture_loader import load_fixture

fixture = load_fixture("default")
inspection = INSPECTION_REGISTRY["B31"]
# each inspection declares spec.min_evidence_items; running against this fixture
# must produce at least that many evidence items.
```

## Extended (P / C / S / X) inspections

The 13 premium / exploratory inspections registered beyond `B01`–`B32` are exercised against this
fixture when the resolved provider exposes the required **capability hooks** — `--provider mock` does,
so a mock run scores them; a vanilla LLM reports `insufficient_evidence` (the same pattern as the
governance cluster, not a failure). They reuse the sections above rather than adding new ones: P01/P08
key off the governance block, C02/C05/C11 partition on `policies.confidence_threshold`, and X04/X11 plus
the judge-path P/S inspections supply their own runner-fixed probes or domain-neutral corpora. The
hook-to-fixture map is in [`../README.md`](../README.md) § *Capability hooks for the extended
inspections*; full descriptions in [`../../../docs/inspection_categories.md`](../../../docs/inspection_categories.md).
