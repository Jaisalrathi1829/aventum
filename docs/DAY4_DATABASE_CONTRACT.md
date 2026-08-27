_Aventum internal contract — binds the Day 4 schema. Design only; migration `0006` does not exist yet._

# Day 4 Database Contract

Smallest coherent schema supporting `simulate → recommend → approve → execute → audit`.

---

## 1. Immutable Prior Entities

Day 4 reads these and **never writes to them**. Any Day 4 code that writes here is a defect.

| Layer | Tables | Guarantee |
|---|---|---|
| Day 2A | `transactions`, `banks`, `ingestion_runs`, `dataset_registry` | observed fact; content hash `13965d76…` must not change |
| Day 2B | `synthetic_gateways`, `synthetic_gateway_profiles`, `synthetic_routing_policies`, `synthetic_routing_policy_gateways`, `synthetic_gateway_health_states`, `synthetic_infrastructure_assignments`, `synthetic_generation_runs` | `baseline-v1` frozen; new *versions* may be added, existing rows never mutated |
| Day 3 | `incidents`, `incident_ground_truth`, `incident_simulation_runs`, `simulated_incident_outcomes`, `incident_analysis_runs`, `incident_anomalies`, `incident_evidence`, `incident_hypotheses`, `incident_rca_results` | diagnosis is history; Day 4 references by ID only |

`incident_ground_truth` is **not readable by any Day 4 module** except a future evaluation harness. The same AST-scan test that guards Day 3's diagnosis path must be extended to cover `aventum_counterfactual`, `aventum_policy`, `aventum_agent`, and `aventum_action`.

---

## 2. Table Set — 7 tables

Each is justified as irreducible below. One merge candidate is noted honestly.

```
agent_runs ──┬──► agent_tool_calls
             │
             └──► counterfactual_simulations ──► recommendations ──► approvals ──► actions
                                                        │                              │
                                                        └──────► audit_events ◄────────┘
```

### 2.1 `counterfactual_simulations`

One row per evaluated candidate policy, **including `NO_ACTION`**.

| Column | Type | Notes |
|---|---|---|
| `simulation_id` | bigserial PK | |
| `incident_id` | bigint FK → `incidents` | |
| `analysis_run_id` | bigint FK → `incident_analysis_runs` | ties to the RCA that motivated it |
| `agent_run_id` | bigint FK → `agent_runs`, NULL | NULL when run deterministically without the agent |
| `candidate_key` | text | e.g. `NO_ACTION`, `reroute:gateway_C→gateway_A@20` |
| `action_type` | text CHECK IN (`NO_ACTION`,`REROUTE`) | |
| `source_gateway_id` / `target_gateway_id` | text FK → `synthetic_gateways`, NULL | NULL for `NO_ACTION` |
| `traffic_percentage` | numeric(5,2) | 0 for `NO_ACTION` |
| `status` | text CHECK IN (`VALID`,`SIMULATION_INVALID`) | |
| `invalid_reason` | text NULL | required when `SIMULATION_INVALID` |
| `affected_population` | integer | |
| `current_distribution` / `projected_distribution` | jsonb | per-gateway counts |
| `projected_success_rate` / `projected_failure_count` | numeric / integer | |
| `projected_gmv_total` / `projected_gmv_retained` / `projected_gmv_at_risk` | numeric(18,2) | from observed amounts |
| `projected_latency_p50` / `_p95` / `latency_delta_ms` | numeric | |
| `concentration_after` | numeric(6,4) | target's post-action traffic share |
| `capacity_utilization` | numeric NULL | **always NULL in Day 4** — see §6 |
| `eligibility_result` | jsonb | per-gateway eligibility as read from Day 2B |
| `held_constant` / `changed_variables` | jsonb | the counterfactual's own audit of itself |
| `assumptions` / `limitations` | jsonb | explicitly labelled OBSERVED / SYNTHETIC / ASSUMED / UNAVAILABLE |
| `simulation_seed` | text | |
| `input_fingerprint` | char(64) | SHA-256 over held-constant inputs — see §5 |
| `simulation_fingerprint` | char(64) | SHA-256 over ordered outputs |
| `model_version` / `policy_version` / `profile_version` | text | |
| `is_simulated` | bool NOT NULL DEFAULT true CHECK (= true) | provenance |
| `created_at` | timestamptz | |

`UNIQUE (incident_id, candidate_key, input_fingerprint)` — re-simulating identical inputs is idempotent and returns the existing row.

**Irreducible because** the recommendation must cite a *persisted, fingerprinted* numeric source. Computing projections on the fly would make fabrication undetectable.

### 2.2 `agent_runs`

| Column | Notes |
|---|---|
| `agent_run_id` bigserial PK | |
| `incident_id`, `analysis_run_id` | FKs |
| `status` | `RUNNING`,`SUCCEEDED`,`AGENT_UNAVAILABLE`,`BUDGET_EXCEEDED`,`FAILED` |
| `model_name`, `model_options` jsonb | must record `think:false` |
| `turns_used`, `tool_calls_used`, `simulations_used` | budget accounting |
| `context_tokens_max` | |
| `started_at`, `finished_at`, `error_message` | |

**Irreducible because** budget enforcement and the `AGENT_UNAVAILABLE` degradation path both need durable state.

### 2.3 `agent_tool_calls`

| Column | Notes |
|---|---|
| `tool_call_id` bigserial PK | |
| `agent_run_id` FK | |
| `sequence` integer | ordering within the run |
| `tool_name` | must be in the registry |
| `request` / `response` jsonb | |
| `outcome` | `SUCCESS`,`NO_DATA`,`INSUFFICIENT_EVIDENCE`,`INVALID_REQUEST`,`SAFETY_BLOCK`,`TIMEOUT`,`INTERNAL_ERROR` |
| `latency_ms`, `attempt`, `created_at` | |

`UNIQUE (agent_run_id, sequence)`.

**Irreducible because** anti-hallucination is only auditable if every number the agent saw is recoverable. This is the table that proves a rationale was grounded.

*Merge candidate:* could fold into `audit_events` as typed rows. Rejected — it would lose queryable columns (`tool_name`, `outcome`, `latency_ms`) that budget accounting and failure analysis need, in exchange for one fewer table.

### 2.4 `recommendations`

| Column | Notes |
|---|---|
| `recommendation_id` bigserial PK | |
| `incident_id`, `analysis_run_id`, `simulation_id` FK | **`simulation_id` NOT NULL** |
| `agent_run_id` FK NULL | NULL ⇒ produced without narrative |
| `action_type`, `source_gateway_id`, `target_gateway_id`, `traffic_percentage` | |
| `expected_success_delta`, `expected_gmv_retained`, `expected_latency_delta_ms`, `risk_score` | **copied from `simulation_id`, never passed in** |
| `confidence`, `evidence_strength`, `significance_sigma`, `severity` | copied from the RCA row (Day 3 P1-2 triple) |
| `supporting_evidence_ids` bigint[] | must resolve in `incident_evidence` |
| `alternatives_considered` jsonb | other candidate simulations + why rejected |
| `rationale` text NULL | the only agent-authored field |
| `policy_validation_result` | `PERMITTED`,`BLOCKED` |
| `policy_reason_codes` jsonb | populated when `BLOCKED` |
| `constraints` jsonb | the gate thresholds in force |
| `status` | see §3 |
| `expires_at` timestamptz NOT NULL | |
| `recommendation_fingerprint` char(64) | |
| `created_at` | |

`UNIQUE (incident_id, simulation_id, policy_version)` — idempotent per (incident, candidate, policy).

**Irreducible.** It is the contract object.

### 2.5 `approvals`

Separate table, **append-only**, so a re-validation cycle adds a row rather than mutating a decision.

| Column | Notes |
|---|---|
| `approval_id` bigserial PK | |
| `recommendation_id` FK | |
| `status` | `PENDING`,`APPROVED`,`REJECTED`,`EXPIRED` |
| `requested_at`, `decided_at`, `expires_at` | |
| `approver_identity` text NULL | required when `APPROVED`/`REJECTED` |
| `decision_note` text NULL | |
| `approval_fingerprint` char(64) | binds to the recommendation content approved |

Partial unique index: at most one `PENDING` approval per recommendation.

**Irreducible because** merging into `recommendations` would require mutating the recommendation row on each decision, destroying the append-only audit property.

### 2.6 `actions`

| Column | Notes |
|---|---|
| `action_id` bigserial PK | |
| `recommendation_id`, `approval_id` FK | |
| `idempotency_key` text UNIQUE NOT NULL | see §4 |
| `adapter_name` | `SimulatedRoutingAdapter` |
| `status` | `PENDING`,`EXECUTED`,`REJECTED`,`FAILED`,`ROLLED_BACK` |
| `rejection_reason` text NULL | machine-readable |
| `revalidation_result` jsonb | what execution re-checked and found |
| `pre_action_metrics` jsonb | snapshot at execution time — Day 5 baseline |
| `actual_simulated_outcome` jsonb | what the adapter modelled |
| `executed_at`, `executed_by`, `created_at` | |
| `is_simulated` bool CHECK (= true) | **Day 4 execution is always simulated** |

**Irreducible.** It is what Day 5 verifies against.

### 2.7 `audit_events`

Append-only spine. **No UPDATE, no DELETE.**

| Column | Notes |
|---|---|
| `event_id` bigserial PK | |
| `incident_id` FK NULL | |
| `event_type` | `AGENT_RUN_STARTED`, `TOOL_CALLED`, `SIMULATION_COMPLETED`, `POLICY_VALIDATED`, `RECOMMENDATION_CREATED`, `APPROVAL_REQUESTED`, `APPROVAL_DECIDED`, `ACTION_EXECUTED`, `ACTION_REJECTED`, … |
| `actor` | `SYSTEM`, `AGENT`, `HUMAN:<identity>` |
| `input_ref` / `output_ref` jsonb | `{table, id}` pointers, not copies |
| `payload` jsonb | structured summary — **never chain-of-thought** |
| `model_version` / `policy_version` / `tool_version` | where applicable |
| `fingerprint` char(64) NULL | |
| `occurred_at` timestamptz NOT NULL | |

**Irreducible.** It is the reconstructable history.

---

## 3. State Machines

```
recommendation:  DRAFT ─► PERMITTED ─► AWAITING_APPROVAL ─► APPROVED ─► EXECUTED
                   │          │                 │              │
                   └─► BLOCKED└─► SUPERSEDED    └─► REJECTED   └─► EXPIRED
                                                                └─► ABANDONED

approval:        PENDING ─► APPROVED | REJECTED | EXPIRED     (terminal, append-only)

action:          PENDING ─► EXECUTED | REJECTED | FAILED ─► ROLLED_BACK
```

Forward-only. Illegal transitions raise. A `BLOCKED` recommendation never reaches approval. `NO_ACTION` recommendations terminate at `PERMITTED` — they need no approval because they change nothing.

---

## 4. Idempotency

| Object | Key | Behavior on repeat |
|---|---|---|
| Simulation | `(incident_id, candidate_key, input_fingerprint)` | returns existing row |
| Recommendation | `(incident_id, simulation_id, policy_version)` | returns existing row |
| Approval | one `PENDING` per recommendation (partial unique index) | second request rejected |
| **Action** | `idempotency_key = SHA256(recommendation_id ‖ approval_id ‖ adapter_name)` UNIQUE | **second execution returns the first result; never executes twice** |

The action-level UNIQUE constraint is the structural defense against duplicate execution — the same mechanism class as Day 3's `incident_key`, and the answer to red-team scenario 8.

---

## 5. Simulation Staleness — `input_fingerprint`

```
input_fingerprint = SHA256(
    incident_id ‖ incident window ‖ incident multipliers ‖
    affected cohort definition ‖ sorted transaction_id set digest ‖
    gateway profiles (baseline-v1) ‖ gateway health states in window ‖
    routing policy version ‖ model_version ‖ config_version ‖ seed
)
```

Recorded on the simulation and **re-derived at recommendation time and again at execution time**. Any mismatch ⇒ the world changed under the simulation ⇒ `STALE_SIMULATION`, execution rejected, re-simulation required.

This is the answer to red-team scenario 10, and it is a *derived* check — it cannot be bypassed by editing a status column.

---

## 6. Capacity — deliberately absent — **P1**

Inspection of the live schema confirms **no capacity column exists anywhere**:

- `synthetic_gateway_profiles`: traffic weight, failure probability, latency multiplier, response mix — no throughput ceiling
- `synthetic_routing_policy_gateways`: traffic weight, `is_eligible`, `eligibility_conditions` (**NULL for all five gateways**) — no capacity
- `synthetic_gateway_health_states`: health + multipliers — no capacity

Therefore:

- `capacity_utilization` is defined in the schema but **must remain NULL in Day 4**, labelled `UNAVAILABLE` in `assumptions`.
- The binding constraint is **concentration**, which *is* derivable from traffic share, not capacity, which is not.
- No Day 4 output may state or imply a capacity headroom figure.

Inventing a capacity number from `baseline_traffic_weight` would be exactly the kind of fabricated production value the project's honesty rules forbid. This is recorded as **P1-1** in the pre-flight review for an explicit decision before coding.

Similarly, `eligibility_conditions` being NULL means eligibility is currently **unconditional** — `get_routing_options` must report `ELIGIBILITY_UNCONDITIONAL` rather than implying a meaningful eligibility check occurred.

---

## 7. Indexes

```sql
ix_sim_incident            (incident_id, candidate_key)
ix_sim_fingerprint         (input_fingerprint)
ix_rec_incident_status     (incident_id, status)
ix_rec_expires             (expires_at) WHERE status = 'AWAITING_APPROVAL'
ix_appr_recommendation     (recommendation_id, status)
ix_action_recommendation   (recommendation_id)
ix_audit_incident_time     (incident_id, occurred_at)
ix_audit_type              (event_type)
ix_toolcall_run            (agent_run_id, sequence)
```

---

## 8. Retry Semantics

| Operation | Retryable | Mechanism |
|---|---|---|
| Simulation | yes, freely | idempotent on `input_fingerprint` |
| Tool call | yes, ≤1 retry, except `SAFETY_BLOCK` | new `agent_tool_calls` row per attempt |
| Recommendation build | yes | idempotent key |
| Approval request | no | one `PENDING` at a time |
| **Execution** | yes, safely | idempotency key returns the original result |
| Audit write | no | append-only; a duplicate would corrupt history |

`SAFETY_BLOCK` is **never** retryable — retrying a safety refusal is how safety gets bypassed.

---

## 9. Migration

Single migration `0006_day4_action_layer.py`. Additive only: 7 new tables, no ALTER on any Day 1/2/3 table, no data rewrite. `alembic check` must report no drift, and the Day 2/3 fingerprint verification must pass unchanged afterwards.
