_Aventum internal contract — binds Day 4 implementation. Design only; no Day 4 code exists yet._

# Day 4 Architecture Contract

Simulate → Recommend → Human Approve → Execute, built on the frozen Day 3 intelligence layer.

---

## 1. Architecture

```
┌─ DAY 3 (frozen, read-only) ────────────────────────────────────────────┐
│  incidents · simulated_incident_outcomes · incident_anomalies          │
│  incident_evidence · incident_hypotheses · incident_rca_results        │
│                              │                                         │
│                    build_handoff(analysis_run_id)                      │
└──────────────────────────────┼─────────────────────────────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │  Day4Handoff (typed) │   PRIMARY alerts, evidence, RCA
                    └──────────┬───────────┘   (no ground truth, by design)
                               ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │  DETERMINISTIC CORE — the only thing allowed to produce numbers   │
   │                                                                   │
   │   counterfactual simulator ──▶ business impact ──▶ policy gate    │
   │   (aventum_counterfactual)     (deterministic)     (deterministic)│
   └───────────────┬───────────────────────────────────┬───────────────┘
                   │ typed tool results                │ PERMITTED / BLOCKED
                   ▼                                   │
        ┌─────────────────────┐                        │
        │  QWEN3 8B AGENT     │  interprets, orchestrates, explains      
        │  (bounded, local)   │  NEVER calculates                        
        └──────────┬──────────┘                        │
                   │ proposal (qualitative + refs)     │
                   ▼                                   ▼
              ┌────────────────────────────────────────────┐
              │  RECOMMENDATION  (numbers from simulator,  │
              │                   rationale from Qwen)     │
              └──────────────────┬─────────────────────────┘
                                 ▼
                        ┌─────────────────┐
                        │ HUMAN APPROVAL  │  mandatory, non-delegable
                        └────────┬────────┘
                                 ▼
                    ┌────────────────────────┐
                    │  EXECUTION ADAPTER     │  revalidates, then simulates
                    │  (SimulatedRoutingAdapter)
                    └────────┬───────────────┘
                             ▼
                    audit_events (append-only) ──▶ DAY 5 verification
```

**Package layout** (mirrors the Day 2A/2B/3 convention):

| Package | Owns |
|---|---|
| `aventum_counterfactual/` | simulator, business impact, optimization objective |
| `aventum_policy/` | deterministic safety gate, action bounds |
| `aventum_agent/` | Qwen client, tool registry, control loop, context builder |
| `aventum_action/` | recommendation, approval, execution adapter, audit |

Four packages, one migration (`0006`). No microservices, no queue, no vector store.

---

## 2. Component Boundaries

| Component | May produce | May never produce |
|---|---|---|
| Simulator | every quantitative projection | narrative, approval |
| Business impact | GMV, success delta, latency delta | risk *verdict* |
| Policy gate | PERMITTED / BLOCKED + reason codes | numbers, narrative |
| Qwen agent | rationale, tool choice, option ranking *commentary* | **any number**, approval, policy override |
| Approval | human decision + identity + timestamp | numbers |
| Execution adapter | action result, audit event | numbers, approval |

**The binding rule:** a numeric field in a `recommendation` row may only be written by copying a value from a persisted `counterfactual_simulations` row. The recommendation builder takes `simulation_id` and reads the numbers from the database — it never accepts numbers as function arguments from the agent layer. This makes fabrication a type error, not a policy violation.

---

## 3. Data Flow

```
analysis_run_id
   → build_handoff()                      [Day 3, existing]
   → agent_run created (budget stamped)
   → tool: get_incident_context           [reads handoff]
   → tool: get_detection_evidence         [reads handoff]
   → tool: get_gateway_health             [reads Day 2B health states]
   → tool: get_routing_options            [reads Day 2B policy + profiles]
   → tool: run_counterfactual × N         [NO_ACTION + bounded alternatives]
   → tool: estimate_business_impact       [deterministic, per simulation]
   → tool: check_action_bounds            [policy gate → PERMITTED/BLOCKED]
   → tool: propose_action                 [builds recommendation from simulation_id]
   → tool: request_human_approval         [creates PENDING approval]
   → [human decides, out of band]
   → execute_action()                     [revalidates everything, then simulates]
   → audit_events throughout
```

---

## 4. Counterfactual Simulator

### 4.1 Why this is a *valid* controlled counterfactual

Day 3 already generates per-transaction simulated outcomes through a single funnel:

```
GatewayRuntimeProfile → failure probability → response family → latency regime → latency value
```

keyed by a deterministic digest of `(transaction_id, incident_key, seed, model_version, config_version)`.

A counterfactual reroute reuses **exactly that machinery** with one variable changed: which gateway a transaction is attributed to. Everything else is held constant *by construction*, not by assertion, because the same transaction rows, amounts, window, incident, and seed lane are reused.

### 4.2 Held constant (enforced, not assumed)

| Invariant | How it is enforced |
|---|---|
| Transaction population | same `transaction_id` set from the incident window |
| Transaction amounts | read from `transactions.amount` (observed, immutable) |
| Payment mix / cohort | cohort definition copied from the anomaly row |
| Observation window | copied from `incidents.incident_start/end` |
| Incident severity | same `incident_id`, same multipliers |
| Gateway health | read from `synthetic_gateway_health_states` for the same window |
| Gateway profiles | `baseline-v1`, frozen since Day 2B |
| Model/config versions | stamped into the simulation fingerprint |
| Seed | derived from `(incident_key, candidate_policy_id)` — deterministic |

### 4.3 Changed — exactly one thing

The **traffic allocation**: which subset of the affected cohort is attributed to which gateway.

Selection of *which* transactions reroute must itself be deterministic — a hash-ordered take of the affected cohort, never a random sample — so the same candidate policy always moves the same transactions.

### 4.4 Outcome regeneration rule

| Transaction | Outcome source |
|---|---|
| Not rerouted | **reuse** the Day 3 `simulated_incident_outcomes` row verbatim |
| Rerouted | **regenerate** through `generate_signals()` using the *target* gateway's profile and health |

Rerouted transactions must not carry the degraded gateway's outcome forward — that would understate the benefit. Non-rerouted transactions must not be regenerated — that would introduce variance the reroute did not cause.

**Approach B still holds:** an observed `FAILED` transaction stays `FAILED` under every candidate policy. A reroute may prevent a *modelled* incident-induced failure; it may never rewrite history.

### 4.5 `SIMULATION_INVALID`

Return a structured invalid result — never a number — when:

- the affected cohort is empty or below `MIN_COHORT_SIZE`
- no eligible target gateway exists
- the target gateway is not `HEALTHY` for the whole window
- the incident window has no health record for a candidate gateway
- the requested traffic percentage exceeds the configured maximum
- the referenced `analysis_run_id` or `incident_id` no longer resolves

---

## 5. Optimization Objective

**Primary:** maximize expected GMV retained.

```
expected_gmv_retained(policy) =
    Σ  amount_t × ( P_success(t | target_gateway)  −  P_success(t | current_gateway) )
   t ∈ rerouted

where P_success(t | g) = 1 − clamp( baseline_failure_probability[g]
                                    × health_failure_multiplier[g, window] )
```

Every term is read from Day 2B profiles, Day 2B health states, and observed `transactions.amount`. No term is estimated by the agent.

**Secondary:** expected success-rate delta on the affected cohort.

**Tie-break:** prefer the *smallest* traffic shift achieving ≥95% of the best candidate's GMV retention. The system prefers the least intervention that captures nearly all the benefit.

**Selection rule:**

```
best = argmax( expected_gmv_retained )  over PERMITTED candidates
if best.expected_gmv_retained < NO_ACTION_MARGIN:  → NO_ACTION
```

Qwen may explain the ranking. Qwen may not alter the objective, the weights, or the margin.

---

## 6. Candidate Set

`NO_ACTION` is **mandatory and always evaluated first**, with a real simulation (not a null row), so the comparison baseline is measured rather than assumed.

Bounded alternatives: reroute **10% / 20% / 30%** of the affected cohort to each eligible healthy gateway.

30% is the ceiling because it is the largest shift that keeps every receiving gateway below the concentration cap in the flagship scenario (gateway_C carries 13.08% of traffic; 30% of it is ~3.9pp moving onto a peer). This is a documented prototype bound, not a claim about real payment infrastructure.

---

## 7. Policy Gate

Deterministic, interpretable, **fail-closed**. Not a blended score.

| Gate | Threshold | Source |
|---|---|---|
| RCA verdict | `CONFIDENT` | `RcaView.verdict` |
| RCA confidence | ≥ 0.60 | `RcaView.confidence` |
| Evidence strength | ≥ 0.50 | `RcaView.evidence_strength` (Day 3 P1-2) |
| Significance | ≥ 6.0 σ | `RcaView.significance_sigma` |
| Severity | ∈ {CRITICAL, HIGH} | `RcaView.severity` |
| Alert role | `PRIMARY` | `DetectionView.alert_role` (Day 3 P1-1) |
| Target eligible | `is_eligible = true` | `synthetic_routing_policy_gateways` |
| Target healthy | `HEALTHY` across the window | `synthetic_gateway_health_states` |
| Traffic shift | ≤ 30% of affected cohort | policy constant |
| Post-action concentration | target ≤ 40% of window traffic | derived from allocation |
| Simulation quality | `VALID` + fingerprint matches | `counterfactual_simulations` |
| Expected benefit | ≥ `NO_ACTION_MARGIN` | simulator |

**All gates must pass.** Any failure → `BLOCKED` with a machine-readable reason code. Every gate reads a value that already exists in Day 2/Day 3 — none requires new measurement.

Requiring both `confidence ≥ 0.60` **and** `evidence_strength ≥ 0.50` **and** `σ ≥ 6.0` is the direct consumption of Day 3's P1-2 fix: no single scalar authorizes an intervention.

---

## 8. Qwen Role

Qwen3 8B, local via Ollama, **`think: false` mandatory** (see §12).

**May:** read typed tool outputs, choose which tool to call next, compare simulation results, identify information gaps, articulate tradeoffs, draft the human-readable rationale, express uncertainty, recommend `NO_ACTION`.

**May not:** compute or restate any number not present verbatim in a tool result; call a tool outside the registry; approve; alter thresholds; convert `BLOCKED` to `PERMITTED`; receive SQL, credentials, raw transaction rows, or ground truth.

Structured output only — a JSON object matching a declared schema, validated before use. A malformed or schema-violating response is a tool failure, not a fallback to prose.

---

## 9. Human Approval

Mandatory for every non-`NO_ACTION` action. `NO_ACTION` requires no approval because it changes nothing.

The approval payload must be understandable **without** the agent's reasoning: it carries the simulation's numbers, the evidence references, the policy gate results, and the explicit alternatives that were considered and rejected.

Lifecycle: `PENDING → APPROVED | REJECTED | EXPIRED`. Approval TTL: **15 minutes** (short, because the underlying incident state can shift).

---

## 10. Execution Adapter

```python
class RoutingActionAdapter(Protocol):
    def apply(self, action: ActionRequest) -> ActionResult: ...
```

Day 4 ships exactly one implementation: `SimulatedRoutingAdapter`, which records the intended routing change and returns a modelled result. **No real payment infrastructure is contacted.**

The Protocol exists so a future `LiveRoutingAdapter` is a registration change, not a rewrite. The recommendation → approval → execution contract is identical for both.

Execution **revalidates from persisted state** — it never trusts an in-memory approval object. See `DAY4_EXECUTION_CONTRACT.md`.

---

## 11. Audit

Append-only `audit_events`, one row per state transition, spanning:

```
incident → evidence → RCA → agent_run → tool_call → simulation
        → policy_validation → recommendation → approval → action
```

Stores **structured rationale and evidence references, never chain-of-thought**. With `think: false` this is enforced by the runtime rather than by policy — Qwen emits no thinking tokens to store.

---

## 12. Performance Budget (measured, not assumed)

Hardware: RTX 4050 Laptop, 6141 MiB VRAM, of which ~5.4 GB is consumed by `qwen3:8b` when loaded.

| Measurement | Result |
|---|---|
| `think: true` (default), 64-token cap | **8,124 ms**, `response: ""`, `done_reason: "length"` — all tokens spent on thinking |
| `think: false`, structured JSON | **1,273 ms**, valid JSON, `done_reason: "stop"`, 13 tokens |

**`think: false` is mandatory**, for three independent reasons: 6.4× faster, no chain-of-thought to store or leak, and it prevents the model from exhausting its token budget before emitting an answer.

| Budget | Limit |
|---|---|
| Max agent turns | 12 |
| Max tool calls per incident | 20 |
| Max simulation calls | 8 (NO_ACTION + up to 7 candidates) |
| Context sent to Qwen | ≤ 8,000 tokens |
| Per-tool timeout | 10 s (30 s for `run_counterfactual`) |
| Qwen per-turn timeout | 30 s |
| Total agent timeout | 180 s |
| End-to-end incident → recommendation | < 300 s |

Exceeding any budget → `AGENT_BUDGET_EXCEEDED`, recommendation state `ABANDONED`, audit event written. **Never a fabricated fallback.**

---

## 13. Failure Modes

| Failure | Behavior |
|---|---|
| Qwen unavailable / OOM | `AGENT_UNAVAILABLE`. Simulations still run and persist; a deterministic recommendation may still be produced **without** narrative rationale. The system degrades to a numbers-only proposal, never to silence or invention. |
| Qwen returns malformed JSON | one retry with a stricter instruction; second failure → `AGENT_UNAVAILABLE` |
| Qwen proposes an unregistered tool | rejected, counted against turn budget, audit event |
| Simulator returns `SIMULATION_INVALID` | that candidate is dropped; if all candidates invalid → `NO_ACTION` |
| Policy gate `BLOCKED` | recommendation persisted as `BLOCKED` with reason codes; no approval requested |
| Approval expires | action rejected at execution; re-validation path required |
| Execution adapter error | action `FAILED`, audit event, no partial state |
| Day 3 handoff missing | hard error before any agent turn |

Every failure path terminates in a persisted, structured state that Day 5 can read.

---

## 14. Production Substitution

```
   SYNTHETIC (today)                    REAL (future)
   aventum_synth generation             live gateway telemetry
            │                                   │
            └──────────► normalized ◄───────────┘
                    telemetry contract
                    (CohortMetrics + GatewayProfile + HealthState)
                              │
              ┌───────────────┴───────────────┐
              │  UNCHANGED ABOVE THIS LINE    │
              │  detection · evidence · RCA   │
              │  simulator · policy · agent   │
              │  recommendation · approval    │
              └───────────────┬───────────────┘
                              ▼
              SimulatedRoutingAdapter  |  LiveRoutingAdapter
```

Day 3's review established that the intelligence layer has **zero** references to synthetic table names. Day 4 must preserve that property: the simulator reads `GatewayProfile` / `HealthState` / `CohortMetrics` *values*, and only `aventum_counterfactual/source.py` may name a synthetic table.

**Replacement boundary:** one metrics CTE (Day 3's `metrics.py`), one profile/health reader (Day 4), one adapter implementation. Nothing in policy, recommendation, approval, or audit changes.

**This is not a claim that real Razorpay integration exists.** It does not.

---

## 15. Day 5 Interfaces

Day 5 receives, by ID and without raw SQL:

`action_id · recommendation_id · incident_id · approval_id · simulation_id · pre_action_metrics · expected_outcome · actual_simulated_outcome · audit_event_ids · rollback_reference`

Verification must recompute metrics using **Day 3's existing `MetricStore`** — the same cohort definition, window semantics, and provenance rules — so "improved" means improved on a like-for-like basis, not a differently-measured one.

`expected_outcome` (from the simulation) and `actual_simulated_outcome` (from execution) are stored separately and must never be conflated: Day 5's job is precisely to compare them.
