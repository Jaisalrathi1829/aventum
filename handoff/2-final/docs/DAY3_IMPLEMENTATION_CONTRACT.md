_Aventum internal contract — binds Day 3 implementation._

# Day 3 Implementation Contract

The smallest complete system that demonstrates: **inject incident → generate simulated outcomes → detect anomaly → collect evidence → produce explainable RCA.** Nothing more. This document is the contract Day 3 implements against — not a design exploration.

Locked by the Day 2B independent review ([DAY2B_ARCHITECTURE_REVIEW.md](DAY2B_ARCHITECTURE_REVIEW.md) P1-1) and the Day 2 closeout gate ([DAY2_FINAL_HANDOFF.md](DAY2_FINAL_HANDOFF.md)).

---

## Day 3 Objective

Build exactly this chain, for one golden scenario:

```
inject incident → generate simulated outcomes → detect anomaly → collect evidence → produce explainable RCA
```

No counterfactual simulator, no recommendation engine, no approval workflow, no execution, no agent/LLM integration, no frontend. Those are Day 4–5.

---

## Golden Incident Scenario (locked)

```text
Affected gateway     : gateway_C
Window               : 3 days
Target degraded rate : 20-25% (baseline 6.421%)
Control group        : gateway_A, gateway_B, gateway_D, gateway_E
Expected signal       : ~9-13 sigma
```

This is not a placeholder — it is measured against the live Day 2 baseline (32,691 baseline transactions on gateway_C, 89.8/day; [DAY2B_ARCHITECTURE_REVIEW.md](DAY2B_ARCHITECTURE_REVIEW.md) §Flagship Cohort Readiness). Do not change it without re-running the detectability math in that section. If Day 3 needs a second scenario for robustness testing, add one — do not replace this one.

---

## Approach B (locked, non-negotiable)

```
Observed historical outcome  ≠  Simulated incident outcome
```

- `transactions.status` (and every other observed field) is **never modified, never overwritten, never reallocated**.
- The incident period gets its own **simulated outcome layer**: new rows, own table(s), own provenance prefix.
- A degradation **adds** simulated failures to the affected cohort. It does **not** redistribute the existing 12,376 observed failures away from healthy gateways.

**Why this is locked, not a preference:** the Day 2B review measured that reallocating observed failures onto a degrading gateway at 25% severity drops the control group to **0.48× its baseline failure rate** — healthy gateways would appear to get *healthier* during the incident, which no real degradation does, and which would make RCA evaluation artificially easy. Full evidence in [DAY2B_ARCHITECTURE_REVIEW.md](DAY2B_ARCHITECTURE_REVIEW.md) §Status-Conditioned Attribution Model, part D.

---

## Tables Day 3 Must Create

Names below are the reference naming; use existing architectural conventions if Day 3's schema design differs, but preserve the concepts and the provenance requirements.

### `incidents`

Ground truth for the injected scenario. **Evaluation-only** — see Non-Negotiable Rule 4.

| Field | Notes |
|---|---|
| `incident_id` | PK |
| `affected_gateway_id` | FK → `synthetic_gateways.gateway_id` |
| `affected_segment` | jsonb, nullable (null = whole-gateway) |
| `incident_type` | e.g. `gateway_degradation` |
| `incident_start`, `incident_end` | timestamptz, half-open window |
| `severity` | the target degraded rate / multiplier used |
| `ground_truth_root_cause` | text — the known cause |
| provenance | `is_evaluation_only boolean NOT NULL DEFAULT true CHECK (is_evaluation_only = true)`, mirroring the `is_synthetic` pattern from Day 2B |

### `simulated_incident_outcomes`

The Approach B outcome layer. One row per transaction **affected by an incident window**, not one row per canonical transaction.

| Field | Notes |
|---|---|
| `transaction_id` | FK → `transactions.transaction_id` (read-only reference, never a write to `transactions`) |
| `incident_id` | FK → `incidents.incident_id` |
| `observed_status` | copied from `transactions.status` at generation time, for convenient comparison — **never used as the value that gets "corrected"** |
| `simulated_status` | the modelled incident-period outcome — may differ from `observed_status` |
| `simulated_latency_ms`, `simulated_response_code` | generated coherently, same chain discipline as Day 2B (`status → response family → latency regime → latency value`) |
| provenance | `is_simulated boolean NOT NULL DEFAULT true CHECK (is_simulated = true)` |

**Coherence requirement inherited from Day 2B:** `simulated_status`, `simulated_latency_ms`, and `simulated_response_code` must be generated through one funnel (the `GatewayRuntimeProfile` pattern or its Day 3 equivalent) so a degraded health window moves all three together — never mutate one signal independently of the others.

### `incident_evidence`

RCA evidence records, computed from the simulated layer plus the canonical/synthetic baseline.

| Field | Notes |
|---|---|
| `evidence_id` | PK |
| `incident_id` | FK |
| `metric` | e.g. `failure_rate`, `latency_p95` |
| `baseline_value`, `incident_value`, `delta` | deterministic, computed — never LLM-estimated |
| `affected_gateway_id`, `affected_segment` | what the evidence is about |
| `control_group_comparison` | jsonb — the control gateways' own metric over the same window |
| `evidence_source` | which table/query produced it, for auditability |

### `incident_evaluation`

The detection + RCA result, kept separate from ground truth.

| Field | Notes |
|---|---|
| `anomaly_id` | PK |
| `detection_window` | tstzrange or start/end |
| `anomaly_score`, `significance` | deterministic statistical measures (e.g. sigma) |
| `affected_population`, `baseline_metrics`, `current_metrics` | jsonb |
| `suspected_root_cause`, `confidence` | the RCA conclusion |
| `supporting_evidence_ids` | array/FK list → `incident_evidence` |
| `alternatives_considered` | jsonb — what else was ruled out and why |
| `explanation` | human-readable, evidence-cited text |

**`incident_evaluation` must never read `incidents.ground_truth_root_cause`.** It is compared against it only afterward, out of band, for scoring Day 3's own accuracy.

---

## Interface Contracts (define now, implement in Day 3)

These are the exact output shapes Day 4 will consume. Do not implement them in this task — this document only fixes the contract.

### Incident
`incident_id · affected_gateway · affected_segment · incident_type · start · end · severity · ground_truth_root_cause · evaluation_only_flag/provenance`

### Simulated outcome
`transaction_id · observed_status · simulated_status · simulated_latency · simulated_response · incident_id · simulation_provenance`

### Detection
`anomaly_id · anomaly_score · detection_window · affected_population · baseline_metrics · current_metrics · significance/evidence_strength`

### RCA evidence
`evidence_id · metric · baseline · incident_value · delta · affected_gateway · affected_segment · control_group_comparison · evidence_source/provenance`

### RCA result
`suspected_root_cause · confidence · supporting_evidence_ids · alternatives_considered · explanation`

---

## What Day 3 Reads From Day 2 (frozen interfaces)

| Source | Access | Notes |
|---|---|---|
| `transactions` | READ ONLY | 250,000 rows, fingerprint `12dec963…f4b8`. No INSERT/UPDATE/DELETE. |
| `banks`, `ingestion_runs`, `dataset_registry` | READ ONLY | Lineage context |
| `synthetic_gateways`, `synthetic_gateway_profiles` (`baseline-v1`) | READ ONLY | Never mutate `baseline-v1`; add a new `profile_version` only if the incident genuinely requires different baseline behavior (it should not — health windows are the intended lever) |
| `synthetic_routing_policies` (`baseline-v1`) | READ ONLY | Same rule |
| `synthetic_gateway_health_states` | **READ + INSERT** | The primary extension point — insert a `DEGRADED` window here for gateway_C over the incident window. No migration required (verified in [DAY2C_INTERFACE_READINESS.md](DAY2C_INTERFACE_READINESS.md) §3). |
| `synthetic_infrastructure_assignments` | READ ONLY | Regenerate through the Day 2B generator if the health window changes; never hand-edit rows |
| `synthetic_generation_runs` | READ ONLY (write via generator) | — |

---

## Out of Scope for Day 3

- Counterfactual simulation ("what if we had rerouted") — Day 4.
- Any recommendation, confidence-bounded action, or approval workflow — Day 4.
- Qwen/Ollama or any LLM/agent integration — Day 4.
- Frontend of any kind — Day 5.
- Rollback/execution/verification-of-action — Day 5.
- A second or generalized incident-injection framework — build the golden scenario correctly first; generalize later only if Day 4/5 need it.

---

## Acceptance Gate for Day 3

Day 3 is complete only when:

1. The golden scenario is injected without modifying any row in `transactions`.
2. `simulated_incident_outcomes` shows an elevated failure rate on gateway_C within the incident window, reaching the 20–25% target.
3. The control group (gateway_A, B, D, E) shows **no** simulated change during the incident window — this is the property Approach A could not deliver.
4. Detection recovers a statistically significant anomaly (~9–13σ, consistent with the locked scenario).
5. Evidence is deterministic and traceable — every `incident_evidence` row cites its source query/table.
6. The RCA result correctly identifies gateway_C, with confidence and cited evidence — measured against `ground_truth_root_cause`, which the RCA pipeline itself never saw.
7. `incidents.ground_truth_root_cause` is never referenced by any query in the detection or RCA code path (audit this explicitly — it is the epistemic boundary the whole project depends on).
8. All Day 2 tests (260) still pass unmodified.
9. New Day 3 tests cover: incident injection determinism, coherence of simulated signals, control-group non-contamination, detection correctness, evidence traceability, and the ground-truth isolation rule in #7.
