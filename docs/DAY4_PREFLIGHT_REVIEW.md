_Aventum internal review — Day 4 architecture pre-flight. Design + review only: no production code, schema, migration, or test was modified._

# Day 4 Pre-Flight Review

---

## Executive Verdict

# DAY 4 ARCHITECTURE READY

The Day 3 handoff is sufficient, the counterfactual is a genuinely controlled comparison, and every quantitative value has a deterministic owner. Four contracts are written and implementable without redesign.

**0 P0.** Two P1 items require an explicit decision before coding — both are *honesty* decisions about data that does not exist, not design gaps:

- **P1-1** — no gateway capacity data exists anywhere in Day 2. The architecture must not imply a capacity check occurred.
- **P1-2** — `eligibility_conditions` is NULL for all five gateways, so eligibility is currently unconditional and must be reported as such.

Both have a recommended resolution below, documented rather than quietly applied. Neither blocks implementation once the decision is recorded.

The single most valuable finding came from actually running the model: **Qwen3 8B with default thinking enabled spent its entire token budget on chain-of-thought and returned an empty response.** `think: false` is mandatory, and it happens to satisfy the "never store chain-of-thought" requirement structurally.

---

## 1. Current Day 3 Handoff

Verified against the live serialized payload and `handoff.py`, not from documentation.

| Object | Fields confirmed present |
|---|---|
| `IncidentView` | `incident_id · incident_name · incident_type · affected_gateway · affected_segment · start · end · severity · status · provenance` |
| `SimulatedOutcomeSummary` | `incident_id · simulation_run_id · rows_in_window · rows_simulated · rows_changed · simulation_fingerprint · provenance` |
| `DetectionView` | `anomaly_id · alert_role · primary_anomaly_id · derived_from_anomaly_id · independence · severity · anomaly_score · significance_sigma · cohort_key · affected_population · baseline_metrics · current_metrics · detection_window · gmv_at_risk · rank` |
| `EvidenceView` | `evidence_id · evidence_type · metric · baseline · current · delta · significance_sigma · cohort · control · source_layer · evidence_source · explanation` |
| `RcaView` | `incident_id · analysis_run_id · verdict · predicted_root_cause · predicted_hypothesis_type · predicted_gateway_id · predicted_segment · confidence · severity · significance_sigma · evidence_strength · summary · explanation · supporting_evidence_ids · contradicting_evidence_ids · alternatives_considered · affected_population · control_population · rca_fingerprint` |
| `Day4Handoff` | `incident · simulation · detections` (PRIMARY only) `· derivative_detections · evidence · rca` |

Two Day 3 P1 fixes are load-bearing for Day 4 and both are present:

- **`alert_role` / `derivative_detections`** — Day 4 acts only on PRIMARY alerts, so a causal shadow can never become the target of a routing change.
- **`severity` + `significance_sigma` + `evidence_strength` beside `confidence`** — the policy gate requires all four, so no single scalar authorizes an intervention.

Ground truth is absent from the payload by construction. **No Day 3 change is required for Day 4.**

---

## 2. Day 4 Architecture

Four packages, one migration, seven tables. Full detail in `DAY4_ARCHITECTURE_CONTRACT.md`.

```
Day 3 handoff → deterministic core (simulator · impact · policy)
                        ↕ typed tools
                   Qwen3 8B agent (interprets, never calculates)
                        ↓
            recommendation → human approval → simulated execution → audit → Day 5
```

The authority chain is enforced by **schema shape**, not by instruction: `propose_action` accepts no numeric fields, so the recommendation builder reads every number from a persisted simulation row server-side.

---

## 3. Counterfactual Validity

This is the question that decides whether Day 4 is science or theatre.

Day 3's simulator already generates per-transaction outcomes through one funnel keyed by a deterministic digest. A counterfactual reroute reuses **that exact machinery** with one variable changed — gateway attribution — so everything else is held constant *by construction* rather than by assertion.

| Held constant | Enforcement |
|---|---|
| transaction population | same `transaction_id` set |
| amounts | observed `transactions.amount` |
| cohort, window, incident, multipliers | copied from Day 3 rows |
| gateway profiles, health | `baseline-v1`, frozen; health read for the same window |
| model/config versions, seed | stamped into the fingerprint |

**Changed:** traffic allocation only. Which transactions move is a hash-ordered take — deterministic, never sampled.

**Outcome rule:** rerouted transactions are *regenerated* under the target gateway's profile; non-rerouted transactions *reuse* their Day 3 outcome verbatim. Regenerating everything would inject variance the reroute did not cause; regenerating nothing would understate the benefit.

Approach B survives: an observed `FAILED` stays `FAILED` under every candidate. A reroute can prevent a modelled incident-induced failure; it can never rewrite history.

**Verdict: valid controlled counterfactual.**

---

## 4. Simulator

Inputs: incident context, affected cohort, current + candidate policy, gateway profiles, health states, deterministic seed, versions. **Never 250K raw transactions** — aggregate and per-cohort structures only.

Outputs: 25 deterministic fields including projected distribution, success rate, failure count, GMV total/retained/at-risk, latency p50/p95/delta, concentration, eligibility result, held-constant and changed-variable manifests, assumptions, limitations, and two fingerprints.

`SIMULATION_INVALID` is returned — never a number — when the cohort is too small, no eligible healthy target exists, the percentage exceeds bounds, or a health record is missing.

---

## 5. Optimization Objective

```
maximize   Σ  amount_t × ( P_success(t | target) − P_success(t | current) )
          t ∈ rerouted
```

every term read from Day 2B profiles, Day 2B health, and observed amounts.

Secondary: success-rate delta. **Tie-break: prefer the smallest traffic shift achieving ≥95% of the best candidate's benefit** — the system prefers the least intervention that captures nearly all the value.

Qwen may explain the ranking; it cannot alter the objective, weights, or margin.

---

## 6. Safety / Policy Gate

Thirteen interpretable gates, all must pass, fail-closed, with machine-readable reason codes. Not a blended opaque score.

Notable: `RCA verdict = CONFIDENT` **and** `confidence ≥ 0.60` **and** `evidence_strength ≥ 0.50` **and** `σ ≥ 6.0` **and** `severity ∈ {CRITICAL, HIGH}` **and** `alert_role = PRIMARY`.

Applying Day 3's flagship numbers (confidence 0.6881, strength 0.7403, σ 9.2575, CRITICAL, PRIMARY): all decision gates pass, so the golden scenario reaches approval — while the Day 3 *marginal* scenario (confidence 0.3980, strength 0.2281, σ 5.16, MEDIUM) fails four gates and is correctly blocked. The gate set discriminates on real Day 3 output.

---

## 7. Qwen Architecture

`qwen3:8b` via Ollama 0.16.1, local, `think: false`, `temperature: 0`, `format: json`.

**Measured on the target RTX 4050 (6141 MiB):**

| Configuration | Result |
|---|---|
| `think: true` (default), 64-token cap | **8,124 ms**, `response: ""`, `done_reason: "length"` — every token spent thinking |
| `think: false` | **1,273 ms**, valid JSON, `done_reason: "stop"` |

Three independent reasons `think: false` is mandatory: 6.4× faster, no chain-of-thought to store or leak, and the model cannot exhaust its budget before answering.

VRAM with the model loaded: ~5.4 GB used, 250–760 MiB free. Functional but tight — the architecture assumes nothing else contends for VRAM, and defines an `AGENT_UNAVAILABLE` path if it OOMs.

---

## 8. Tool Contracts

Nine typed tools, fully specified in `DAY4_AGENT_TOOL_CONTRACT.md` with purpose, schemas, authority, side effects, failure modes, timeout, retry, idempotency, and provenance.

`get_incident_context` · `get_detection_evidence` · `get_gateway_health` · `get_routing_options` · `run_counterfactual` · `estimate_business_impact` · `check_action_bounds` · `propose_action` · `request_human_approval`

Qwen receives **no** SQL, credentials, connection strings, raw transactions, or ground truth. Seven outcome codes; `SAFETY_BLOCK` is never retryable.

---

## 9. Human Approval

Mandatory for every non-`NO_ACTION` action, 15-minute TTL, append-only rows. The payload is decision-complete without agent reasoning and carries an explicit `provenance` field telling the approver this is a synthetic incident with simulated execution.

Qwen cannot approve: no tool, no code path, no writable column.

---

## 10. Execution

`SimulatedRoutingAdapter` only. `actions.is_simulated` is `CHECK (= true)` — the database refuses to record a Day 4 execution as real.

Nine-step revalidation from **persisted state**, including re-deriving `input_fingerprint` from current world state and re-running the full policy gate. Both are derived checks, so neither can be satisfied by editing a status column.

---

## 11. Action Expiry / Staleness

Recommendation TTL 30 min; approval TTL 15 min (shorter, because approval judges a current world state). Seven staleness conditions, all routing to the same remedy: re-simulate → re-validate → re-approve. No override exists.

---

## 12. Audit

Append-only `audit_events`, 15 event types, full ID-traversable chain from incident to rollback. Structured rationale and evidence references only — never chain-of-thought, which `think: false` means is never produced.

Retries add rows; they never overwrite. Every state transition stays reconstructable.

---

## 13. Database Design

Seven tables, each justified as irreducible in `DAY4_DATABASE_CONTRACT.md`: `counterfactual_simulations`, `agent_runs`, `agent_tool_calls`, `recommendations`, `approvals`, `actions`, `audit_events`.

One merge candidate (`agent_tool_calls` into `audit_events`) was considered and rejected with the tradeoff documented.

Additive migration `0006`. No ALTER on any Day 1/2/3 table. All prior entities explicitly listed as immutable, and the AST ground-truth scan must be extended to the four new packages.

---

## 14. Failure Modes

Qwen unavailable → deterministic recommendation without narrative. Malformed JSON → one retry, then `AGENT_UNAVAILABLE`. `SIMULATION_INVALID` → drop candidate; all invalid → `NO_ACTION`. Policy `BLOCKED` → no approval requested. Approval expired → execution rejects. Budget exceeded → `ABANDONED`.

Every path ends in a persisted structured state Day 5 can read. **No fabricated fallback anywhere.**

---

## 15. Performance

| Stage | Target |
|---|---|
| Read tools | < 1 s (Day 3 measured RCA read at 0.2 ms) |
| `run_counterfactual` | < 10 s (Day 3 full simulation: ~330 ms for 2,093 rows) |
| Qwen per turn | < 5 s typical (measured 1.27 s for a short structured reply) |
| Incident → recommendation | < 300 s |

Day 3's measured end-to-end analysis was ~2.1 s uncontended, so the deterministic half of Day 4 has ample headroom. The agent is the latency-dominant component.

---

## 16. Production Substitution

Day 3's review established the intelligence layer has **zero** references to synthetic table names. Day 4 preserves this: only `aventum_counterfactual/source.py` may name a synthetic table.

Replacement boundary is three files: the metrics CTE, the profile/health reader, and the adapter implementation. Policy, recommendation, approval, execution lifecycle, and audit are unchanged.

**No claim of real Razorpay integration is made anywhere.**

---

## 17. Day 5 Continuity

Day 5 receives `action_id · recommendation_id · incident_id · approval_id · simulation_id · pre_action_metrics · expected_outcome · actual_simulated_outcome · cohort_definition · window · audit_event_ids · rollback_reference`.

Verification must recompute through Day 3's `MetricStore` so before/after are measured identically. `expected_outcome` and `actual_simulated_outcome` are stored separately — the gap between them is what Day 5 measures. Day 5 must be able to conclude the action did *not* help.

---

## 18. Red-Team Findings

| # | Attack | Defense | Result |
|---|---|---|---|
| 1 | Qwen invents a 30% improvement | `propose_action` accepts **no numeric fields**; the builder reads numbers from the persisted `simulation_id` server-side | **BLOCKED** — structurally impossible, not merely forbidden |
| 2 | Reroute to an unhealthy gateway | `TARGET_NOT_HEALTHY` gate reads `synthetic_gateway_health_states` for the full window; re-checked at execution | **BLOCKED** |
| 3 | Stale human approval | Execution re-reads persisted approval; `now > expires_at` → `APPROVAL_EXPIRED`; in-memory object never trusted | **BLOCKED** |
| 4 | Incident disappears after recommendation | `input_fingerprint` re-derived at execution; mismatch → `STALE_SIMULATION`; plus `INCIDENT_NO_LONGER_ACTIVE` | **BLOCKED** |
| 5 | Malicious text in transaction/evidence fields | Tool results delivered in a `tool` role as data; thresholds are code constants unreachable from any prompt; agent has no tool to change limits, approve, or execute | **BLOCKED** — worst case is a suspicious rationale string, which cannot move a number or pass a gate |
| 6 | Simulator says `NO_ACTION` is better | `NO_ACTION` is simulated first as a real baseline; selection requires beating it by `NO_ACTION_MARGIN`; it needs no approval and is a success state | **SUPPORTED** — the system can decline |
| 7 | Qwen unavailable | Simulations and policy gate are agent-independent; deterministic recommendation still produced with `rationale = NULL` | **SAFE DEGRADATION** |
| 8 | Duplicate execution commands | `idempotency_key` UNIQUE on `actions`; second call returns the original result, adapter invoked exactly once | **BLOCKED** at the database |
| 9 | Candidate exceeds concentration | `CONCENTRATION_EXCEEDS_BOUND` gate on post-action share; re-checked at execution | **BLOCKED** — *but see P1-1: capacity itself cannot be checked* |
| 10 | Simulation inconsistent with its inputs | `input_fingerprint` re-derived and compared at both recommendation and execution | **BLOCKED** |

Ten scenarios, ten defenses, each anchored to a specific structural mechanism rather than a policy statement.

---

## 19. P0 Issues

**None.**

---

## 20. P1 Issues

### P1-1 — No gateway capacity data exists

*Evidence:* live inspection of `synthetic_gateway_profiles`, `synthetic_routing_policy_gateways`, and `synthetic_gateway_health_states` confirms **no capacity, throughput, or headroom column anywhere**. Day 2B modelled traffic *weights*, not capacity.

*Why it matters:* the pre-flight brief requires "never assume unlimited gateway capacity" and asks whether policy can reject a capacity violation. It cannot — the data does not exist. Deriving a capacity ceiling from `baseline_traffic_weight` would fabricate a production value, which the project's honesty rules forbid.

*Recommended resolution (requires explicit sign-off):* declare capacity `UNAVAILABLE`. Keep `capacity_utilization` in the schema but always NULL; make **concentration** the binding allocation constraint, since traffic share *is* derivable; forbid any Day 4 output from stating or implying a capacity figure. The alternative — adding a synthetic capacity model to Day 2B — is a Day 2 change and out of Day 4 scope.

### P1-2 — Eligibility is unconditional

*Evidence:* `eligibility_conditions` is NULL for all five gateways in `baseline-v1`; `is_eligible = true` for all.

*Why it matters:* an eligibility "check" that always passes must not be presented as a substantive control. Doing so would overstate the safety envelope.

*Recommended resolution:* `get_routing_options` returns `eligibility_basis: "ELIGIBILITY_UNCONDITIONAL"`, and the `TARGET_NOT_ELIGIBLE` gate is documented as *structurally present but currently non-binding*. The gate stays in the code so a future conditional policy activates it without a redesign.

---

## 21. P2 Issues

| # | Item | Why deferrable |
|---|---|---|
| P2-1 | VRAM headroom is 250–760 MiB with the model loaded | works today; `AGENT_UNAVAILABLE` path covers OOM |
| P2-2 | Numeric-grounding test (every number in `rationale` appears in a tool result) not yet specified as a test | cheap to add during Day 4; the structural defense does not depend on it |
| P2-3 | 30% reroute ceiling is a documented prototype bound, not derived from capacity | honest as stated; revisit only if capacity data ever exists |
| P2-4 | `agent_tool_calls` could fold into `audit_events` | tradeoff documented; current split is more queryable |
| P2-5 | Rollback is contract-only in Day 4 | Day 5 owns the trigger decision |
| P2-6 | All ten Day 3 P2 items remain open | unchanged by this review |

---

## 22. Final Architecture Gate

| # | Question | Answer |
|---|---|---|
| 1 | Implementable from the contracts without redesign? | **Yes** |
| 2 | Valid controlled counterfactual? | **Yes** — one variable changed, rest held by construction |
| 3 | `NO_ACTION` first-class? | **Yes** — simulated as a real baseline, needs no approval |
| 4 | Deterministic tools authoritative for every quantitative value? | **Yes** — `propose_action` takes no numbers |
| 5 | Qwen3 8B within 6 GB? | **Yes, measured** — 1.27 s with `think: false`; tight VRAM |
| 6 | Qwen prevented from unrestricted DB access? | **Yes** — nine typed tools, no SQL, no credentials |
| 7 | Dangerous actions bounded by deterministic policy? | **Yes** — 13 fail-closed gates |
| 8 | Human approval mandatory? | **Yes** for every non-`NO_ACTION` action |
| 9 | Execution simulated and idempotent? | **Yes** — `is_simulated CHECK`, UNIQUE idempotency key |
| 10 | Stale recommendations rejectable? | **Yes** — fingerprint re-derivation + full gate re-run |
| 11 | Day 5 can verify recovery correctly? | **Yes** — same `MetricStore`, expected vs actual kept separate |
| 12 | Real telemetry replaceable without rebuilding intelligence? | **Yes** — three-file boundary |
| 13 | Audit reconstructable? | **Yes** — append-only, ID-traversable |
| 14 | Free of unnecessary scope? | **Yes** — no RAG, no vector DB, no queue, no microservices |
| 15 | Any unresolved P0/P1? | **2 P1**, both honesty decisions with recommended resolutions; neither blocks coding once signed off |

---

## 23. Final Decision

# DAY 4 ARCHITECTURE READY

No P0. The two P1 items are decisions about how to represent **absent data honestly**, not gaps in the design — and both have a recommended resolution that keeps the system truthful. Recording those two decisions is the only thing standing between this contract set and implementation.

---

_Method: live schema inspection of Day 2B gateway/routing/health tables; live serialized Day 4 handoff; direct read of `handoff.py`, `rca.py`, `detect.py`, `hypothesis.py`; two measured Qwen3 8B generations on the target GPU; Day 3 report and review cross-reference. No production code, schema, migration, or test was modified. No Day 4 implementation was started._
