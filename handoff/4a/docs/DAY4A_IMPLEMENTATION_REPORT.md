_Aventum internal report — Day 4A, the deterministic decision core._

# Day 4A Implementation Report

`Day 3 incident → counterfactual simulation → business impact → NO_ACTION comparison → deterministic policy → recommendation → human approval → simulated execution → audit`

Implemented **without Qwen, without Ollama, without a tool registry, and without an agent loop.** Those are Day 4B and are asserted absent by a test, not merely omitted.

---

## Executive Summary

The deterministic spine is complete and works end to end on the real 250,000-row canonical dataset. Given the flagship gateway_C incident, Aventum now simulates 13 candidate policies (NO_ACTION plus 10/20/30% reroutes to four eligible gateways), computes deterministic business impact and decomposed risk, selects a candidate against a measured NO_ACTION baseline, validates it through 13 fail-closed policy gates, persists a recommendation whose every number is read server-side from the simulation row, requires an explicit human approval, revalidates all persisted state at execution time, executes exactly once through a simulated adapter, and leaves a reconstructable append-only audit trail with full provenance back to the source file's SHA-256.

**The Day 4A thesis, demonstrated:** every recommendation produced carries `rationale = NULL` and `agent_run_id = NULL`. The system decides correctly with no language model present at all. Day 4B's agent will be an explanation layer over a spine that already works — not a dependency the decisions rest on.

| Result | Value |
|---|---|
| Migration | `0006` — 7 tables, additive only, chain reproducible from clean state |
| Tests | **472 passed, 0 failed, 0 skipped** (378 pre-existing + 94 new) |
| Red-team | **12/12** blocked or handled safely, verified against the real dataset |
| Incident → recommendation | **152.7 ms** (target: < 300 s) |
| Canonical fingerprint | `12dec963…f4b8` — unchanged |
| Day 2B generation fingerprint | `e8414edd…2fe3c8` — unchanged |
| Observed content MD5 | `2674c4d8d0452469687b8e19022efd19` — unchanged before and after |

**Two genuine defects were found during implementation** — one by my own database constraint, one by inspecting a suspicious result. Both are documented in full below (§4, §26). The second was serious: it inflated projected reroute benefit roughly 5×.

---

## 1. Migration

`backend/migrations/versions/0006_day4_action_layer.py` — seven tables per `DAY4_DATABASE_CONTRACT.md` §2: `agent_runs`, `agent_tool_calls`, `counterfactual_simulations`, `recommendations`, `approvals`, `actions`, `audit_events`.

**Additive only.** No `ALTER`, no `DROP`, no data rewrite on any Day 1/2/3 table. Verified live: the canonical fingerprint, the Day 2B generation fingerprint, the row count, and a content MD5 over all 250,000 rows are byte-identical before and after.

**Chain reproducibility**, proven on a scratch database so the canonical load was never at risk:

```
upgrade head    → 0006 (head)
downgrade base  → (empty)
upgrade head    → 0006 (head)
```

`agent_runs` and `agent_tool_calls` are created despite Day 4A having no agent, because `counterfactual_simulations.agent_run_id` and `recommendations.agent_run_id` are nullable FKs into them. Defining the target now means Day 4B attaches an agent run without a migration; every Day 4A row leaves the FK `NULL`, which is exactly the contract's "produced deterministically, without narrative" case.

### Three safety properties live in the schema, not in application code

| Constraint | Guarantee |
|---|---|
| `uq_action_idempotency` UNIQUE on `actions.idempotency_key` | Concurrent executions are serialised by PostgreSQL; exactly one row can exist, so the adapter runs exactly once |
| `uq_approval_one_pending` partial unique index `WHERE status='PENDING'` | At most one approval outstanding per recommendation — an approval cannot be raced |
| `uq_simulation_identity` on `(incident_id, candidate_key, input_fingerprint)` | Re-simulating identical inputs converges on the existing row instead of minting a second, divergent projection a recommendation could cite selectively |

Plus `CHECK (is_simulated = true)` on both `counterfactual_simulations` and `actions`: the database refuses to record a Day 4 projection or execution as real. The honesty boundary is a constraint.

---

## 2. Simulator

`backend/aventum_counterfactual/` — `source.py` (telemetry boundary), `simulator.py`, `impact.py`, `risk.py`, `optimize.py`, `fingerprint.py`, `models.py`, `constants.py`.

`source.py` is **the only Day 4 module permitted to name a synthetic table**, preserving the property Day 3's review established: the intelligence layer holds zero references to `synthetic_*` names, so a real-telemetry feed could be substituted without touching analytical code. A test enforces this across all 20 Day 4 modules.

---

## 3. Counterfactual Validity

**Held constant** (persisted on every row in `held_constant`, so the claim is checkable rather than trusted): transaction population, transaction IDs, amounts, payment mix, cohort definition, incident, incident window, incident multipliers, gateway health, gateway profiles, model/config versions, policy version, seed semantics, eligibility assumptions.

**Changed — exactly one thing:** traffic allocation. A test asserts `changed_variables` never grows a second independent key.

The world is read **once** per sweep and shared across all 13 candidates, so every option is evaluated against an identical world. Re-reading per candidate would let the world drift mid-sweep and make the comparison meaningless.

`SIMULATION_INVALID` is returned — with a structured reason and **every metric left NULL** — when the cohort is empty or below `MIN_COHORT_SIZE`, no eligible target exists, the target is ineligible or not healthy across the window, no health record covers it, the requested traffic exceeds the ceiling, or the incident/analysis run no longer resolves. Verified: an invalid row carries no `projected_gmv_retained` and no `projected_success_rate`. A refusal is never dressed up as a number.

---

## 4. Probability Consistency

**There is no Day 4 failure model.** `build_runtime_profile`, `added_failure_probability`, and `is_in_affected_cohort` are imported from `aventum_incident.simulate`; `generate_signals` and `GatewayRuntimeProfile` from `aventum_synth.outcome_model`; the outcome digest from `aventum_incident.rng.incident_digest_for`. `P_success(t | gateway, incident state)` is defined once, as `1 − GatewayRuntimeProfile.effective_failure_probability`.

Three tests pin this:

- `test_day4_and_day3_compute_identical_success_probabilities` — for every gateway × health state, Day 4's profile and Day 3's produce bit-identical `effective_failure_probability`, `effective_latency_multiplier`, and `effective_response_mix()`.
- `test_no_action_reproduces_day3_outcomes_exactly` — NO_ACTION reroutes nothing, so every projected status, latency, and response code equals the Day 3 stored row.
- `test_no_action_projected_failure_count_matches_day3` — the aggregate matches a direct SQL count against `simulated_incident_outcomes`.

That last property is what makes the whole comparison meaningful: candidates are measured against a **real simulated baseline**, not an assumed one.

**Outcome regeneration rule.** Rerouted rows regenerate through `generate_signals()` under the *target* gateway's profile and health. Non-rerouted rows reuse the Day 3 row **verbatim**. Both halves matter: regenerating an unmoved row would inject variance the reroute did not cause; carrying a moved row's degraded outcome forward would understate the benefit.

A pleasing consequence of reusing Day 3's own cohort predicate rather than reimplementing it: for a **gateway** incident a reroute escapes the blast radius, but for an **issuer** incident it does not, because moving gateway does not change `sender_bank`. Rerouting correctly fails to fix an issuer problem, and no special-case code was needed to achieve that.

**Approach B holds.** An observed `FAILED` transaction stays `FAILED` under every candidate at every percentage — asserted directly. A reroute may prevent a modelled incident-induced failure; it can never rewrite history. `transactions` is read-only throughout, verified by content MD5 before and after a full flow.

---

## 5. NO_ACTION

Simulated **first and always**, as a full row over the same cohort with the same machinery — never a null, never an implied zero, never skipped for speed.

It is gated differently from interventions, and deliberately so. NO_ACTION changes nothing, so the gates bounding a *change* (target health, eligibility, traffic shift, concentration, benefit margin) have no subject, and the gates justifying *intervening* (the Day 3 evidence quartet, alert role) are beside the point. **If the evidence gates applied to NO_ACTION, weak evidence would block the safe option** and leave the system with nothing honest to recommend — inverting the entire reason NO_ACTION exists as a first-class candidate. It is gated on simulation validity and freshness only.

`test_no_action_is_permitted_even_with_weak_evidence` pins this: given `verdict=INSUFFICIENT_EVIDENCE, confidence=0.0`, NO_ACTION is still PERMITTED.

NO_ACTION terminates at `PERMITTED`, requires no approval and no execution, and is a **successful** outcome.

---

## 6. Business Impact

All deterministic, all derived, none passed in: affected transactions, projected success rate, projected failure count, success-rate delta, projected GMV total, expected GMV retained, GMV at risk, projected latency p50/p95, latency delta, traffic redistribution, concentration before/after, blast radius.

GMV derives from `transactions.amount` — verified against a direct SQL sum. **Amounts are observed; which transactions succeed is modelled.** The field is named `expected_gmv_retained`, never "recovered GMV", and a test asserts the string `recovered gmv` appears nowhere in the Day 5 handoff.

Two populations are kept distinct: success rates and GMV are measured over the **affected cohort**; concentration is measured over the **full window**, because a gateway's traffic share is only meaningful against all traffic.

---

## 7. Optimization

```
expected_gmv_retained(policy) = Σ  amount_t × (P_success(t|target) − P_success(t|current))
                                t ∈ rerouted
```

Implemented exactly, and verified term by term against an independent recomputation in `test_expected_gmv_retained_matches_the_contract_formula`, which also asserts non-rerouted rows contribute exactly zero.

**It is an expectation, not a flip count.** A count of transactions whose modelled status changed is one realisation of a random draw; the expectation is the quantity that draw samples, and it is stable under reseeding. Optimising on the noisy realisation would let a lucky seed outrank a genuinely better policy. The live run shows why this matters: at 20% and 30% the *realised* success delta can coincide while the expectations differ substantially.

**Ordered selection rule:** maximise expected GMV retained → maximise success-rate delta → prefer the **smallest traffic shift reaching ≥95%** of the best candidate's benefit. Without the third rule the optimiser always reaches for the largest permitted shift; with it, a 10% reroute capturing 96% of a 30% reroute's benefit wins. Both branches are tested (`test_tie_break_prefers_the_smallest_shift_reaching_95_percent`, `test_tie_break_does_not_fire_below_95_percent`).

**`NO_ACTION_MARGIN = 1000.0` INR** — the one genuinely new constant in Day 4A, and the open decision the pre-flight left unresolved. Derivation: the flagship cohort projects ~19,000 INR retained at a 30% reroute, so 1,000 INR is roughly 5% of a real intervention's benefit — comfortably below a genuine win, comfortably above the numerical noise of a marginal one. It is a **documented prototype constant, not a calibrated business threshold**, and it is owned by `aventum_policy`; the simulator never decides whether a benefit is "enough".

---

## 8. Risk Model

Six named components, each a pure function of persisted inputs: `concentration_risk`, `target_health_risk`, `latency_risk`, `simulation_quality_risk`, `evidence_uncertainty_risk`, `routing_uncertainty_risk`. Determinism is tested by recomputation.

**The aggregate score is never a gate.** Gates bind on individual measurable constraints, so a comfortable aggregate can never wash out one unacceptable component — the same structural lesson as Day 3's P1-2 fix, applied to risk. The persisted payload says so explicitly (`aggregate_is_advisory_only: true`).

**`capacity_risk` is reported `UNAVAILABLE` and excluded from the aggregate**, never silently treated as 0.0. A zero would read as "capacity checked, no risk found" — a claim about a check that never happened. A test asserts it is the string `UNAVAILABLE` and specifically **not** `0.0`.

`routing_uncertainty_risk` is a fixed 0.25 and labelled honestly as a stand-in for "this dimension cannot currently be assessed", because `eligibility_conditions` carries no discriminating information.

---

## 9. Capacity and Eligibility Honesty (P1-1, P1-2 resolved)

**Capacity.** `capacity_utilization` is `NULL` on every simulation row — asserted across a whole sweep. `assumptions.capacity = "UNAVAILABLE"`, the approval payload's `expected_risk.capacity = "UNAVAILABLE"`, the adapter result carries it, and the Day 5 handoff carries it. Capacity is **not a policy gate**, and the stored policy decision states `capacity_gate: "ABSENT — no capacity telemetry exists; not evaluated"` so a reader of a persisted decision never has to wonder whether an unmentioned check silently passed. Concentration is the binding allocation constraint.

**Eligibility.** Every gateway reports `basis: ELIGIBILITY_UNCONDITIONAL`, asserted for all five. The gate remains architecturally present for a future real rule set; what it does not do is imply a substantive check occurred.

---

## 10. Policy Gate

`backend/aventum_policy/` — thresholds in `constants.py`, evaluation in `gate.py`. Thirteen gates, all must pass, fail-closed, each returning a machine-readable reason code plus the observed value and the bound applied.

| # | Gate | Requirement |
|---|---|---|
| 1 | `simulation_status` | `VALID` |
| 2 | `simulation_freshness` | `input_fingerprint` re-derived from the current world matches |
| 3 | `rca_verdict` | `CONFIDENT` |
| 4 | `rca_confidence` | ≥ 0.60 |
| 5 | `evidence_strength` | ≥ 0.50 |
| 6 | `significance_sigma` | ≥ 6.0 |
| 7 | `severity` | CRITICAL or HIGH |
| 8 | `alert_role` | `PRIMARY` |
| 9 | `target_eligible` | `is_eligible = true` |
| 10 | `target_healthy` | HEALTHY across the whole trafficked window |
| 11 | `traffic_shift` | ≤ 30% |
| 12 | `post_action_concentration` | ≤ 40% |
| 13 | `expected_benefit` | ≥ `NO_ACTION_MARGIN` |

`test_every_evidence_gate_fails_closed_independently` proves each of the Day 3 quartet blocks **on its own** — that is the P1-2 property, that no single strong signal can carry a weak one.

**Thresholds are unreachable from any caller.** `validate()`'s signature carries no threshold, no weights, no override, and no force flag — asserted by introspection in `test_thresholds_cannot_be_supplied_by_a_caller`. The only way to change one is to edit `constants.py` and bump `POLICY_VERSION`, which invalidates every recommendation validated under the old version and is re-checked at execution.

Live result on the flagship incident: **all 13 gates PASS** (confidence 0.6881, evidence strength 0.7403, 9.2575σ, CRITICAL, PRIMARY, 30% shift, 0.2972 concentration, 19,126.26 INR benefit).

---

## 11. Recommendation Integrity

```python
build_recommendation(session, *, simulation_id, analysis_run_id, world,
                     alert_role, rationale=None, agent_run_id=None, alternatives=None, now=None)
```

**There is no numeric parameter.** No `expected_gmv_retained`, no `risk_score`, no `confidence`, no `traffic_percentage`. Every quantitative field is read server-side from `simulation_id` and from the RCA row.

This is deliberately **not** "validate the caller's numbers against the simulation". A validation can be skipped, mis-scoped, or fooled by a rounding tolerance; an absent parameter cannot be passed. Tested two ways — by introspection (no forbidden parameter name exists) and adversarially (passing `expected_gmv_retained=999999.0` raises `TypeError`) — plus a field-by-field equality assertion against the simulation row.

The only caller-authored field is `rationale`. It is `NULL` throughout Day 4A, which is the proof that the spine does not depend on narrative.

**State machine** is forward-only with every legal transition enumerated; illegal transitions raise. `BLOCKED` is terminal and can never reach approval — asserted. `NO_ACTION` terminates at `PERMITTED` and refuses an approval request.

---

## 12. Human Approval

Interface: `backend/aventum_action/cli.py` — `request`, `approve`, `reject` as **three separate commands**, deliberately not one command with an `--auto-approve` flag. Such a flag would be exactly the affordance that erodes a human gate: it exists, it is convenient, and eventually it is always on. `--approver` is mandatory on both decision commands, and the database enforces the same rule via `ck_approval_decision_coherent`.

The payload is decision-complete without any agent reasoning: proposed action, source/target, traffic percentage, expected benefit **with its basis** (`OBSERVED_TRANSACTION_AMOUNTS + MODELLED_OUTCOMES`), expected risk with components, the Day 3 quartet presented as four separate signals, evidence refs, simulation ID and fingerprints, every rejected alternative with the specific reason it lost, all 13 gates with observed values, expiry, and:

```json
"provenance": "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION"
```

carried **inside** the artifact, so the honesty boundary survives the payload being exported, screenshotted, or pasted into a ticket.

Approvals are append-only in sequence, fingerprint-bound to the recommendation content shown, expiring at **15 minutes** (deliberately shorter than the recommendation's 30, because an approval is a judgement about a *current* world), and human-attributed. Only one `PENDING` per recommendation, enforced both in code and by the partial unique index — both tested.

---

## 13. Simulated Execution

`RoutingActionAdapter` is a `Protocol`; `SimulatedRoutingAdapter` is the only implementation. A future `LiveRoutingAdapter` implements the same one-method interface without changing the recommendation, approval, policy, or audit contracts.

The result is **measurable, not a status flag**: traffic moved, resulting allocation, post-action success rate, failure rate, failure count, GMV total, GMV at risk, latency p50/p95, execution fingerprint, reference simulation fingerprint, timestamp, provenance.

Critically, the adapter **re-derives** those metrics from the projected outcome population rather than echoing the simulation's summary. Echoing would make `actual_simulated_outcome` a copy of `expected_outcome` by construction, and the gap between them — the only thing Day 5 exists to measure — would be identically zero and meaningless. A test asserts the two dicts differ.

**No recovery claim is made anywhere.** The adapter notes explicitly: *"Whether it constitutes recovery is Day 5's judgement, not this adapter's."*

---

## 14. Execution Revalidation

Execution takes **IDs, not objects** — a caller cannot hand in a mutated recommendation or a hand-built approval, because neither is accepted as input. Fourteen checks run inside the executing transaction, all recorded on the action row:

`recommendation_exists` · `recommendation_approved` · `approval_exists` · `approval_approved` · `approval_not_expired` · `recommendation_not_expired` · `approval_fingerprint_matches` · `simulation_valid` · `simulation_fresh` · `policy_version_unchanged` · `policy_revalidated` (full 13-gate re-run) · `target_healthy` · `target_eligible` · `idempotency_key_claimed`

Two of these are **derived**, which is what makes a stale action impossible to execute: the input fingerprint is recomputed from the current world, and the full policy gate is re-run against current data. Neither can be satisfied by editing a status column.

Any failure → `REJECTED` with a machine-readable reason. **No partial execution** — proven by `test_rejected_execution_never_invokes_the_adapter`, which passes an adapter that raises if called and confirms it never runs. There is no override path and no force flag.

---

## 15. Idempotency

`idempotency_key = SHA256(recommendation_id ‖ approval_id ‖ adapter_name)`, UNIQUE on `actions`. Verified against a hand-computed digest.

The action row is **INSERTed before the adapter runs**. That ordering is the guarantee: the INSERT carries the unique key, so two callers contend at the database rather than in application timing. A repeated execution returns the original `ActionResult` unchanged and writes an `ACTION_DUPLICATE_SUPPRESSED` audit event; the adapter is invoked exactly once.

---

## 16. Concurrency

`test_concurrent_execution_runs_the_adapter_exactly_once` uses **two real threads, two real sessions, two real connections** to the real database, synchronised on a barrier so both attempt the same idempotency key simultaneously. A counting adapter subclass records invocations.

Result: **1 adapter invocation, 1 action row, both callers receive the same result, exactly one marked duplicate.** The guarantee demonstrably comes from PostgreSQL's unique-constraint serialisation, not from timing luck.

---

## 17. Audit

Append-only. `audit.py` exposes `emit()` and nothing else — no update function, no delete function, no "correct the last event" helper — so an audit trail cannot be revised by any Day 4 code path.

Events emitted: `SIMULATION_COMPLETED`, `SIMULATION_INVALID`, `POLICY_VALIDATED`, `RECOMMENDATION_CREATED`, `RECOMMENDATION_BLOCKED`, `APPROVAL_REQUESTED`, `APPROVAL_DECIDED`, `APPROVAL_EXPIRED`, `ACTION_EXECUTED`, `ACTION_REJECTED`, `ACTION_DUPLICATE_SUPPRESSED`, `ACTION_ROLLED_BACK`. (`AGENT_RUN_STARTED`, `TOOL_CALLED`, `SUSPECTED_PROMPT_INJECTION` are defined for Day 4B and unused here.)

`input_ref`/`output_ref` are `{table, id}` **pointers, not row copies**, so the trail cannot drift from the data it describes — asserted. Actor is `SYSTEM` or `HUMAN:<identity>`; a test confirms an approval by `alice` records `HUMAN:alice`.

**No chain-of-thought.** A test scans every payload for `chain_of_thought`, `reasoning_trace`, `<think>`, and `thinking`. In Day 4A this is trivially satisfied — no model runs — and in Day 4B it will be satisfied structurally by `think:false`.

---

## 18. Provenance

`provenance_chain(action_id)` traverses by ID:

```
action → approval → recommendation → simulation → analysis run → incident
       → generation run → source ingestion run → canonical fingerprint → source SHA-256
```

Every link verified non-null on the real dataset. Layers are labelled OBSERVED / SYNTHETIC / SIMULATED, with `answer_key: "EXCLUDED — evaluation only; never read by any Day 4 module"`.

**Ground-truth isolation extended to Day 4.** The Day 3 AST guard now scans all 20 modules across `aventum_counterfactual`, `aventum_policy`, and `aventum_action`, with docstrings stripped, asserting none names `incident_ground_truth`, `IncidentGroundTruth`, or `ground_truth` in executable code. **Zero exemptions** — the layer key was renamed to `answer_key` specifically so the guard could stay absolute rather than carve out a special case.

---

## 19. Staleness

```
input_fingerprint = SHA256(incident ‖ window ‖ multipliers ‖ cohort ‖ sorted transaction-set digest
                           ‖ profiles ‖ health windows ‖ eligibility ‖ policy version
                           ‖ model version ‖ config version ‖ seed)
```

Deliberately **independent of the candidate**, so a whole sweep shares one freshness token and staleness is a property of the *world*, not of one option.

Re-derived at simulation, recommendation, and execution. Any mismatch → `STALE_SIMULATION`, execution rejected. The transaction-set digest hashes `(id, amount, observed_status)` triples — hashing IDs alone would miss a canonical row being altered underneath the simulation, which must never happen but which this should still catch if it ever did.

Recovery is always the same: **re-simulate → re-validate → re-approve.** No override.

---

## 20. Day 5 Handoff

`build_verification_handoff(action_id)` returns: `action_id`, `recommendation_id`, `approval_id`, `incident_id`, `analysis_run_id`, `simulation_id`, `pre_action_metrics`, `expected_outcome`, `actual_simulated_outcome`, `cohort_definition`, `measurement_window`, `approver_identity`, `executed_by`, `executed_at`, fingerprints, `audit_event_ids`, `rollback_reference`.

`pre_action_metrics` is snapshotted at **execution time**, not at simulation time, because Day 5 must compare against the world as it was when the action actually happened.

`expected_outcome` and `actual_simulated_outcome` are separate keys and are never merged — the gap between them is the subject of Day 5. `cohort_definition` and `measurement_window` travel alongside so Day 5 measures the same population the same way; a differently-measured "after" would make any improvement claim meaningless.

`verification_note` states plainly that Day 4A makes **no recovery claim**, and that establishing whether the action helped — with the option of concluding it did not — is Day 5's responsibility.

**Rollback** is a forward transition: `EXECUTED → ROLLED_BACK`, the original row is never deleted, and `ACTION_ROLLED_BACK` is emitted. Day 5 owns the trigger decision.

---

## 21. Red-Team Results

All twelve run against the **real 250,000-row dataset and the real flagship incident**, not fixtures.

| # | Attack | Result | Mechanism |
|---|---|---|---|
| 1 | Fake recommendation numbers | **BLOCKED** | `TypeError` — no such parameter exists |
| 2 | Execute without approval | **BLOCKED** | `RECOMMENDATION_NOT_APPROVED` |
| 3 | Modify world after approval | **BLOCKED** | `STALE_SIMULATION` (re-derived fingerprint) |
| 4 | Target unhealthy gateway | **BLOCKED** | `TARGET_NOT_HEALTHY`; all metrics NULL |
| 5 | Exceed concentration bound | **BLOCKED** | `CONCENTRATION_EXCEEDS_BOUND` |
| 6 | Replay identical action | **BLOCKED** | 1 action row; duplicate suppressed |
| 7 | Change policy version after approval | **BLOCKED** | `POLICY_VERSION_CHANGED` |
| 8 | Modify recommendation after approval | **BLOCKED** | `APPROVAL_FINGERPRINT_MISMATCH` |
| 9 | Set `is_simulated = false` | **BLOCKED** | `CheckViolation ck_action_is_simulated` |
| 10 | Ground-truth access from Day 4 | **BLOCKED** | 0 of 20 modules name it |
| 11 | Impossible simulator request | **BLOCKED** | `TRAFFIC_EXCEEDS_MAXIMUM`; all metrics NULL |
| 12 | NO_ACTION superior | **SUPPORTED** | NO_ACTION selected — the system can decline |

**12/12 blocked or handled safely.**

---

## 22. Tests

**472 passed, 0 failed, 0 skipped.** 378 pre-existing Day 1–3 tests, all still passing unmodified; 94 new in `backend/tests/test_decision_core.py`.

No prior test was deleted, weakened, or skipped. The only change to existing test infrastructure was adding the seven Day 4 tables to `conftest.py`'s `TRUNCATE` list.

Regression assertions specifically prove Day 4 writes to no prior layer: content MD5 over all of `transactions` unchanged across a full flow; MD5 over `simulated_incident_outcomes` unchanged; MD5 over `synthetic_gateway_profiles` (`baseline-v1`) unchanged.

`test_no_qwen_or_agent_code_exists_in_day4a` asserts the scope boundary rather than assuming it: no `aventum_agent` package, and no `import ollama`, `qwen3:8b`, or `localhost:11434` in any Day 4A module.

---

## 23. Performance

Measured on the real 250,000-row dataset (2,093 transactions in the incident window, 264-transaction affected cohort):

| Stage | Time |
|---|---|
| Load world state (window read) | 41.9 ms |
| NO_ACTION simulation | 23.5 ms |
| Single counterfactual (20% reroute) | 8.1 ms |
| Complete candidate sweep (13 simulations) | 85.2 ms |
| Policy validation (13 gates) | 0.9 ms |
| **Full incident → recommendation** | **152.7 ms** |
| Approval request | 6.0 ms |
| Approval decision | 2.7 ms |
| Simulated execution + full revalidation | 12.9 ms |
| Duplicate execution (suppressed) | 1.8 ms |

The demo path target is < 300 s; the deterministic spine uses **0.05%** of it, leaving essentially the entire budget to Day 4B's agent (which is itself budgeted at ≤180 s).

Structurally: one set-based query per world load, no query inside any loop (no N+1), no per-row commits, and a candidate sweep that is O(candidates × cohort) with a fixed small candidate set. Migration `0006` applies in under a second — it creates empty tables and writes no data.

---

## 24. Limitations

Stated on every simulation row in `limitations`, and repeated here:

- **Capacity** — no gateway capacity telemetry exists in this dataset. Not estimated, not inferred, not gated on.
- **Eligibility** — unconditional under `baseline-v1`. No substantive eligibility check occurs.
- **GMV** — amounts observed, outcomes modelled. Projected GMV retained, never recovered GMV.
- **Execution** — simulated. No real payment infrastructure exists or is contacted.
- **Post-action outcome** — the dataset is static, so any post-action state is a modelled continuation rather than a measured one. This constrains Day 5 as much as Day 4.
- **`NO_ACTION_MARGIN = 1000.0` INR** — a documented prototype constant, not a calibrated business threshold.
- **`routing_uncertainty_risk = 0.25`** — an honest stand-in for an unassessable dimension, not a measurement.
- **Day 2B P2-1 inherited** — no failure ever lands in the NORMAL latency regime, so latency and failure-rate signals remain redundant in one direction. Unchanged by Day 4A.
- **Rollback** — implemented as a state transition and audit event; Day 5 owns the trigger decision.

---

## 25. Day 4B Dependencies

Everything Day 4B needs is in place and unused:

- `agent_runs` / `agent_tool_calls` tables exist; `agent_run_id` FKs are nullable on both `counterfactual_simulations` and `recommendations`.
- `rationale` is the single agent-writable field on `recommendations`, currently `NULL`.
- Audit event types `AGENT_RUN_STARTED`, `TOOL_CALLED`, `SUSPECTED_PROMPT_INJECTION` are defined and unemitted.
- Every deterministic function the nine planned tools must call is implemented and tested: `load_world_state`, `load_rca`, `load_primary_anomalies`, `run_counterfactual`, `compute_business_impact`, `compute_risk`, `validate`, `build_recommendation`, `request_approval`.
- The `AGENT_UNAVAILABLE` degradation path is already the normal case — Day 4A *is* the system running without an agent.

**The structural constraint Day 4B must preserve:** `propose_action` must map onto `build_recommendation`, which accepts no numeric field. The agent gets `rationale` and nothing else.

---

## 26. Contract Contradictions and Defects Found

Two genuine defects were found during implementation. Both are recorded rather than quietly patched.

### Defect 1 — reroute selection was correlated with the incident outcome draw (SERIOUS)

`_selection_rank` originally hashed `transaction_id | incident_key | seed | model_version | config_version` — **byte-identical** to Day 3's `incident_assignment_key`, including matching `1.0.0` version values. Both therefore produced the same SHA-256.

Day 3 flips an observed SUCCESS to FAILED when `lane_uniform(digest, LANE_OUTCOME)` — the digest's leading 8 bytes — falls below `p_add`. Sorting by that same digest's hex ascending ordered the cohort by *exactly the quantity that decided failure*. The result on the flagship incident: a 10% reroute selected 26 transactions of which **26 had failed**, and a 20% reroute rescued every single incident-induced failure. Projected benefit was overstated roughly **5×**.

It was caught by noticing that a 10% reroute reported 26 flips from 26 rerouted rows — a 100% rescue rate that no probabilistic model should produce.

**Fix:** a domain separator, `_SELECTION_DOMAIN = "AVENTUM_REROUTE_SELECTION_V1"`, prefixed onto the selection payload, making the selection stream independent of the outcome stream. Post-fix the flagship shows 2/26, 4/52, and 9/79 flips — consistent with `p_add ≈ 0.17`.

Pinned by `test_reroute_selection_is_independent_of_the_incident_outcome_draw` and `test_selection_rank_differs_from_the_day3_outcome_key`.

This is worth stating plainly: a selection correlated with outcomes is not a routing policy any real system could implement, because it would require knowing which transactions were going to fail.

### Defect 2 — `ck_action_executed_coherent` was too strict

Written as `(status = 'EXECUTED') = (executed_at IS NOT NULL)`, which rejected `ROLLED_BACK` — an action that legitimately retains its `executed_at`, because it genuinely *was* executed before being reverted. Erasing that would destroy the "we acted, then reverted" history rollback exists to preserve. Corrected to `(status IN ('EXECUTED','ROLLED_BACK')) = (executed_at IS NOT NULL)`.

Found by the constraint itself failing the rollback test — the schema catching a schema error.

### Also corrected during implementation

- **JSONB null semantics.** SQLAlchemy maps Python `None` to JSON `null`, not SQL `NULL`, so `policy_reason_codes IS NOT NULL` was TRUE for a PERMITTED recommendation and `ck_rec_reason_codes_coherent` refused the first real run. Fixed with `JSONB(none_as_null=True)`. Again: the constraint caught it.
- **Health-window coverage.** The original whole-window check required a health record spanning the *declared* incident window. Day 2B writes health windows spanning the dataset's own time range, and an incident window defined on calendar boundaries can extend slightly past the last transaction — producing `TARGET_NO_HEALTH_RECORD` for a demonstrably healthy gateway. Now bound to `traffic_span`, the sub-interval that actually carries traffic: *healthy at every moment we would actually be routing payments*. A genuine mid-window degradation still fails, because it overlaps real traffic.

### Contract note (not a contradiction)

`DAY4_EXECUTION_CONTRACT.md` §5 lists nine revalidation steps; the implementation records **fourteen** named checks. The extra five are decompositions (e.g. "recommendation exists" and "recommendation is APPROVED" as separate checks) that make a rejection reason precise rather than generic. No contract step is omitted. No contract amendment was required.

---

## Verdict

The deterministic decision core is complete, verified against the real canonical dataset, and safe under adversarial testing. Day 4B may begin.
