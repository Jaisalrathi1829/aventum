_Aventum internal contract — binds the Day 4 action layer. Design only; execution is SIMULATED throughout Day 4._

# Day 4 Execution Contract

Recommendation → policy validation → human approval → simulated execution → audit.

---

## 1. Recommendation Lifecycle

```
DRAFT ──► PERMITTED ──► AWAITING_APPROVAL ──► APPROVED ──► EXECUTED
  │           │                 │                 │
  └► BLOCKED  └► SUPERSEDED     └► REJECTED       └► EXPIRED
                                                  └► ABANDONED
```

| State | Meaning |
|---|---|
| `DRAFT` | built from a simulation, not yet validated |
| `PERMITTED` | passed every policy gate |
| `BLOCKED` | failed ≥1 gate; carries `policy_reason_codes`; terminal |
| `AWAITING_APPROVAL` | approval requested |
| `APPROVED` / `REJECTED` | human decided |
| `EXECUTED` | adapter completed |
| `EXPIRED` | TTL elapsed before execution |
| `SUPERSEDED` | a newer recommendation for the same incident replaced it |
| `ABANDONED` | agent budget exceeded or internal error |

A `NO_ACTION` recommendation terminates at `PERMITTED`. It requires no approval and no execution, because it changes nothing — and that is a **successful** outcome, not a degraded one.

**TTL: 30 minutes** from creation.

---

## 2. Policy Validation

Runs at `DRAFT → PERMITTED|BLOCKED`, and **again at execution** (§5). Deterministic, interpretable, fail-closed.

| Gate | Requirement | Reason code on failure |
|---|---|---|
| RCA verdict | `CONFIDENT` | `RCA_NOT_CONFIDENT` |
| RCA confidence | ≥ 0.60 | `CONFIDENCE_BELOW_THRESHOLD` |
| Evidence strength | ≥ 0.50 | `EVIDENCE_STRENGTH_BELOW_THRESHOLD` |
| Significance | ≥ 6.0 σ | `SIGNIFICANCE_BELOW_THRESHOLD` |
| Severity | CRITICAL or HIGH | `SEVERITY_BELOW_THRESHOLD` |
| Alert role | `PRIMARY` | `ALERT_NOT_PRIMARY` |
| Simulation status | `VALID` | `SIMULATION_INVALID` |
| Simulation freshness | `input_fingerprint` matches now | `STALE_SIMULATION` |
| Target eligible | `is_eligible = true` | `TARGET_NOT_ELIGIBLE` |
| Target healthy | `HEALTHY` across the whole window | `TARGET_NOT_HEALTHY` |
| Traffic shift | ≤ 30% | `TRAFFIC_SHIFT_EXCEEDS_BOUND` |
| Post-action concentration | ≤ 40% | `CONCENTRATION_EXCEEDS_BOUND` |
| Expected benefit | ≥ `NO_ACTION_MARGIN` | `BENEFIT_BELOW_NO_ACTION_MARGIN` |

**All gates must pass.** Thresholds are constants in `aventum_policy`; they are not parameters the agent, the recommendation, or the approval payload can influence.

Requiring confidence **and** evidence strength **and** significance **and** severity together is the direct consumption of Day 3's P1-2 fix: no single scalar can authorize an intervention.

**Capacity is not a gate**, because no capacity data exists (`DAY4_DATABASE_CONTRACT.md` §6). Concentration is the binding allocation constraint, and no output may imply a capacity check occurred.

---

## 3. Approval Lifecycle

```
PENDING ──► APPROVED | REJECTED | EXPIRED     (terminal; rows are append-only)
```

**Mandatory** for every non-`NO_ACTION` action. Qwen cannot approve; there is no tool, no code path, and no column it can write.

**TTL: 15 minutes** — deliberately shorter than the recommendation TTL, because an approval is a judgement about a *current* world state.

The approval payload must let a human decide without reading agent reasoning:

```json
{
  "recommendation_id": 42, "incident_id": 1,
  "proposed_action": "REROUTE",
  "source_gateway": "gateway_C", "target_gateway": "gateway_A",
  "traffic_percentage": 20,
  "expected_benefit": { "gmv_retained": 14203.50, "success_delta": 0.031,
                        "basis": "OBSERVED_TRANSACTION_AMOUNTS + MODELLED_OUTCOMES" },
  "expected_risk":    { "latency_delta_ms": 41, "concentration_after": 0.298,
                        "capacity": "UNAVAILABLE" },
  "decision_inputs":  { "confidence": 0.6881, "evidence_strength": 0.7403,
                        "significance_sigma": 9.2575, "severity": "CRITICAL" },
  "evidence_refs": [12, 15, 18],
  "simulation_id": 7, "simulation_fingerprint": "…",
  "alternatives_rejected": [ {"candidate": "NO_ACTION", "why": "…"},
                             {"candidate": "reroute@30", "why": "CONCENTRATION_EXCEEDS_BOUND"} ],
  "gates": [ … ],
  "expires_at": "…",
  "provenance": "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION"
}
```

Note `provenance` — the approver is told, in the payload itself, that this is a synthetic incident and execution will be simulated.

Re-validation after expiry creates a **new** approval row. Decisions are never overwritten.

---

## 4. Execution Lifecycle

```
PENDING ──► EXECUTED | REJECTED | FAILED ──► ROLLED_BACK
```

Day 4 executes through `SimulatedRoutingAdapter` only. **No real payment infrastructure is contacted, and no claim of real integration is made.**

```python
class RoutingActionAdapter(Protocol):
    name: str
    def apply(self, action: ActionRequest) -> ActionResult: ...
```

A future `LiveRoutingAdapter` implements the same Protocol. The recommendation, approval, policy, and audit contracts are unchanged by that substitution.

---

## 5. Execution Revalidation — the critical step

Execution **must not trust an approval object it is handed.** It re-reads persisted state and re-checks everything, in this order, before the adapter is invoked:

1. Recommendation exists and is `APPROVED`
2. Approval exists, is `APPROVED`, and `now < approval.expires_at`
3. `now < recommendation.expires_at`
4. `approval_fingerprint` matches the recommendation's current content — approval was for *this* recommendation, not an edited one
5. `simulation.input_fingerprint` **re-derived from current world state** and compared — catches an incident, cohort, health state, or profile that changed after simulation
6. Policy gate **re-run in full** against current data
7. `policy_version` unchanged since validation
8. Target gateway still `HEALTHY` and `is_eligible`
9. Idempotency key not already present

Any failure → `REJECTED` with a machine-readable reason. **No partial execution.**

Steps 5 and 6 are what make a stale action impossible to execute: both are *derived* checks, so neither can be satisfied by editing a status column.

---

## 6. Expiry & Staleness

| Condition | Detection | Result |
|---|---|---|
| Recommendation expired | `now > expires_at` | `RECOMMENDATION_EXPIRED` |
| Approval expired | `now > expires_at` | `APPROVAL_EXPIRED` |
| Incident context changed | `input_fingerprint` mismatch | `STALE_SIMULATION` |
| Target health changed | health re-read | `TARGET_NOT_HEALTHY` |
| Policy version changed | version compare | `POLICY_VERSION_CHANGED` |
| Bounds changed | gate re-run | `BOUNDS_CHANGED` |
| Incident resolved/withdrawn | incident status re-read | `INCIDENT_NO_LONGER_ACTIVE` |

Every stale path routes to the same remedy: **re-simulate → re-validate → re-approve**. There is no override.

---

## 7. Idempotency

```
idempotency_key = SHA256(recommendation_id ‖ approval_id ‖ adapter_name)
```

`UNIQUE NOT NULL` on `actions`. A duplicate execution request returns the original `ActionResult` unchanged and writes an `ACTION_DUPLICATE_SUPPRESSED` audit event. The adapter is invoked **exactly once** — enforced by the database, not by application logic.

---

## 8. Rejection Conditions

`RECOMMENDATION_NOT_APPROVED` · `RECOMMENDATION_EXPIRED` · `APPROVAL_EXPIRED` · `APPROVAL_FINGERPRINT_MISMATCH` · `STALE_SIMULATION` · `POLICY_REVALIDATION_FAILED` · `POLICY_VERSION_CHANGED` · `TARGET_NOT_HEALTHY` · `TARGET_NOT_ELIGIBLE` · `INCIDENT_NO_LONGER_ACTIVE` · `DUPLICATE_EXECUTION`

Each is persisted on the action row and emitted as an audit event.

---

## 9. Rollback

Day 4 defines the contract; Day 5 owns the decision to invoke it.

```
EXECUTED ──► ROLLED_BACK
```

`rollback(action_id, reason)` restores the prior routing allocation through the same adapter, is itself idempotent, and emits `ACTION_ROLLED_BACK`. It never deletes the original action row — rollback is a forward transition, so the history of "we acted, then reverted" stays reconstructable.

Day 5 triggers rollback when post-action verification shows the intended improvement did not materialize.

---

## 10. Audit Events

Append-only. Emitted at every transition:

`AGENT_RUN_STARTED` · `TOOL_CALLED` · `SIMULATION_COMPLETED` · `SIMULATION_INVALID` · `POLICY_VALIDATED` · `RECOMMENDATION_CREATED` · `RECOMMENDATION_BLOCKED` · `APPROVAL_REQUESTED` · `APPROVAL_DECIDED` · `APPROVAL_EXPIRED` · `ACTION_EXECUTED` · `ACTION_REJECTED` · `ACTION_DUPLICATE_SUPPRESSED` · `ACTION_ROLLED_BACK` · `SUSPECTED_PROMPT_INJECTION`

Each row carries `actor` (`SYSTEM` / `AGENT` / `HUMAN:<identity>`), `input_ref`, `output_ref`, version stamps, and a structured `payload`. **Chain-of-thought is never stored** — and with `think: false` none is produced.

The full chain is reconstructable by ID traversal:

```
incident → analysis_run → agent_run → tool_calls → simulation
        → policy_validation → recommendation → approval → action → rollback
```

---

## 11. Day 5 Verification Inputs

Day 5 receives:

| Field | Purpose |
|---|---|
| `action_id`, `recommendation_id`, `approval_id`, `incident_id`, `simulation_id` | identity |
| `pre_action_metrics` | baseline snapshot taken at execution time |
| `expected_outcome` | what the simulation projected |
| `actual_simulated_outcome` | what the adapter modelled |
| `cohort_definition`, `window` | so Day 5 measures the same population |
| `audit_event_ids` | full history |
| `rollback_reference` | if reversion is needed |

**Verification must recompute using Day 3's `MetricStore`** — identical cohort definition, window semantics, and provenance rules. Comparing a differently-measured "after" against the "before" would make any improvement claim meaningless.

`expected_outcome` and `actual_simulated_outcome` are stored separately and must never be merged: the gap between them is precisely what Day 5 exists to measure.

**"Numbers changed" ≠ "recovery succeeded."** Day 5 must establish improvement relative to the pre-action baseline, on the same cohort, with the control group still available as a comparison — and must be able to conclude that the action did *not* help.

---

## 12. Honesty Boundary

Every artifact in this layer describes a **synthetic incident** and a **simulated execution**:

- No real payment infrastructure is contacted at any point in Day 4.
- `actions.is_simulated` is `CHECK (= true)` — the database refuses to record a Day 4 execution as real.
- GMV figures use observed `transactions.amount`; *which* transactions succeed is modelled. Outputs say `expected_gmv_retained`, never "recovered GMV".
- Capacity is reported `UNAVAILABLE`, never estimated.
- A demo must never imply that a synthetic incident occurred in production or that a real gateway was rerouted.
