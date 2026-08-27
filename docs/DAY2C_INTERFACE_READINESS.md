_Aventum internal design document — binding handoff specification._

# Day 2C Interface Readiness

What Day 2C (incident injection + ground truth) may depend on, what it must preserve, and the one architectural decision it must adopt before writing code.

Produced by the Day 2B independent review ([DAY2B_ARCHITECTURE_REVIEW.md](DAY2B_ARCHITECTURE_REVIEW.md)). Verified against the live schema and a measured 250,000-row generation, not copied from the Day 2B report.

---

## 1. Tables Day 2C may depend on

### Read-only (owned by Day 2A)

| Table | Rows | Day 2C may |
|---|---|---|
| `transactions` | 250,000 | **READ ONLY** — never INSERT/UPDATE/DELETE |
| `banks` | 8 | READ ONLY |
| `ingestion_runs` | 1 (`SUCCEEDED`) | READ ONLY — lineage |
| `dataset_registry` | 1 | READ ONLY — resolve `source_dataset` → SHA-256 |
| `v_transactions_canonical` | — | READ ONLY |

### Read-write (owned by Day 2B, extendable by Day 2C)

| Table | Rows | Day 2C may |
|---|---|---|
| `synthetic_gateway_health_states` | 5 | **INSERT** degradation windows — this is the primary extension point |
| `synthetic_gateway_profiles` | 5 | INSERT a new `profile_version`; never mutate `baseline-v1` |
| `synthetic_routing_policies` / `_policy_gateways` | 1 / 5 | INSERT a new `policy_version`; never mutate `baseline-v1` |
| `synthetic_generation_runs` | 1 | INSERT via the generator; never hand-edit |
| `synthetic_infrastructure_assignments` | 250,000 | Regenerate through the generator; never hand-edit |
| `synthetic_gateways` | 5 | READ ONLY in practice — the universe is fixed at 5 |

### New tables Day 2C must create

`incidents`, `incident_evidence`, and any incident-evaluation tables. These do **not** exist yet — verified absent from the live schema.

---

## 2. Keys and relationships

**Join key:** `transactions.transaction_id` (`text`, PK, 250,000 distinct, 100% unique).

**Critical:** `transaction_id` is a **global** primary key, not scoped per dataset. Day 2C must not assume `(source_dataset, transaction_id)`.

Existing FK graph Day 2C inherits:

```
transactions.transaction_id ──(ON DELETE CASCADE)──► synthetic_infrastructure_assignments
ingestion_runs.ingestion_run_id ─────────────────────► synthetic_infrastructure_assignments
                                                     └► synthetic_generation_runs
synthetic_generation_runs ──(ON DELETE CASCADE)─────► synthetic_infrastructure_assignments
                                                     └► synthetic_gateway_health_states
synthetic_gateways.gateway_id ──────────────────────► assignments / profiles / health / policy_gateways
synthetic_routing_policies.policy_version ──────────► assignments / policy_gateways
```

Incident tables should reference `synthetic_gateways.gateway_id` and `synthetic_generation_runs.generation_run_id`, **not** `transaction_id` directly — an incident is a property of infrastructure over a time window, not of an individual payment.

---

## 3. The degradation extension point (no migration required)

`synthetic_gateway_health_states` already carries everything a time-bounded degradation needs. Verified live DDL:

| Column | Type | Day 2C use |
|---|---|---|
| `gateway_id` | text FK | which gateway degrades |
| `generation_run_id` | bigint FK, CASCADE | binds the window to a generation |
| `health_state` | text, CHECK IN (`HEALTHY`,`DEGRADED`,`UNAVAILABLE`) | `DEGRADED` / `UNAVAILABLE` already permitted |
| `valid_from`, `valid_to` | timestamptz, CHECK `valid_to > valid_from` | incident window (half-open) |
| `failure_multiplier` | numeric(8,4), CHECK > 0 | raises failure probability |
| `latency_multiplier` | numeric(8,4), CHECK > 0 | raises latency |
| `timeout_multiplier` | numeric(8,4), CHECK > 0 | shifts response mix toward infrastructure-side |
| `reason` | text | human-readable cause |

**Confirmed by test and code inspection:** all three multipliers funnel through a single object (`GatewayRuntimeProfile` in `outcome_model.py`), so raising them moves failure probability, latency regime, **and** response mix together. Day 2C does not mutate each signal independently.

`UNAVAILABLE` is already in the CHECK vocabulary — no migration to use it.

---

## 4. Invariants Day 2C must preserve

1. **Never write to `transactions`.** No synthetic column may be added to it. (Currently 16 columns, all observed/derived — verified.)
2. **Never set `is_synthetic = false`** on any synthetic table. All seven reject it at the database level (adversarially verified).
3. **Incident ground truth must never enter the diagnosis path.** It exists solely for offline evaluation ([DAY2B_TRUTH_MODEL.md](DAY2B_TRUTH_MODEL.md)).
4. **Never mutate `baseline-v1`** profiles or policy. Add a new version instead — the `UNIQUE (gateway_id, profile_version)` constraint supports this.
5. **Determinism must survive.** Any new randomness must use `aventum_synth.rng` lane `LANE_RESERVED` (lane 3, currently unused and reserved precisely for this). Never `random`, `numpy.random`, or Python's salted `hash()`.
6. **The generation fingerprint must change when incident inputs change**, and must remain reproducible for a fixed set of inputs.
7. **Calibration reference stays a parameter source.** No row of the Nigerian dataset may be imported or joined.
8. **Regenerate, don't patch.** Incident effects must come from re-running the generator with a degraded health window, not from `UPDATE`s on assignment rows.

---

## 5. THE decision Day 2C must adopt first

### Recommendation: **Approach B — generate a synthetic incident-outcome layer**

Day 2B's status-conditioned assignment preserves observed marginals *exactly*: the number of failures in any window is fixed by `transactions.status`. Concentrating failures onto a degrading gateway therefore **removes** them from healthy gateways.

Measured on a real 3-day window (2024-06-01 → 06-04 IST; 2,093 transactions, 109 observed failures, gateway_C holding 264 transactions at 7.20%):

| Target gateway_C rate | Failures moved onto C | Control-group rate | Control vs baseline |
|---|---|---|---|
| 10% | 7 | 4.54% | 0.92× |
| 15% | 21 | 3.77% | 0.77× |
| 20% | 34 | 3.06% | 0.62× |
| **25%** | **47** | **2.35%** | **0.48×** |
| 30% | 60 | 1.64% | 0.33× |

**Approach A would make the healthy gateways appear roughly twice as healthy during the incident.** No real gateway degradation improves its peers. That artifact:

- is physically implausible and would be visible to any reviewer;
- **inflates the contrast an RCA engine sees**, making detection easier than reality — so a passing RCA evaluation would overstate real capability;
- worsens as incident severity rises, exactly where a credible demo needs the most severity;
- caps achievable severity (at 30% the control group is nearly failure-free, and beyond ~35% the window runs out of failures entirely).

**Approach B** keeps `transactions` immutable but generates incident-period outcomes in a Day 2C-owned synthetic layer, so a degradation *adds* failures rather than *moving* them.

| Criterion | Approach A (reallocate) | Approach B (synthetic outcome layer) |
|---|---|---|
| Epistemic correctness | Observed marginals preserved, but incident semantics distorted | Observed data untouched; incident outcomes clearly labelled synthetic |
| Realism | Poor — control group improves during incident | Good — control group stays flat |
| RCA evaluation quality | **Biased optimistic** — inflated contrast | Unbiased; difficulty is controllable |
| Counterfactual compatibility | Poor — no headroom to model "what if we rerouted" | Good — counterfactuals are natural in a generated-outcome model |
| Auditability | Simple, but the artifact needs explaining every time | Requires a clear observed-vs-simulated boundary, which the truth model already defines |
| Implementation complexity | Lower | Moderate — one new outcome column/table plus provenance |
| Demo credibility | Weak under scrutiny | Strong |

**Required framing for Approach B:** incident-period rows carry a *simulated* outcome that must never be presented as the observed historical outcome. The observed `transactions.status` remains the historical record; the simulated outcome is a modelled counterfactual for the incident scenario. Both must be visible and distinguishable in any read surface, exactly as `observed_*` / `synthetic_*` already are.

This is a **review recommendation**, not an implementation. Day 2C owns the design.

---

## 6. Recommended flagship incident

Recomputed independently (not taken from the Day 2B report):

| Property | Value |
|---|---|
| **Affected cohort** | `gateway_C` (all traffic) |
| Baseline volume | 32,691 transactions (13.08% of traffic), 89.8/day |
| Baseline failure rate | 6.42% |
| **Window** | 3 days (~268 transactions on gateway_C) |
| **Degraded rate** | 20–25% |
| Detection threshold (3σ, 3-day) | 10.9% |
| **Expected signal** | ≈9–13σ |
| **Control group** | gateway_A (65,145 / 3.94%), gateway_B (67,365 / 5.16%), gateway_D (52,597 / 4.68%), gateway_E (32,202 / 5.52%) |

**Why gateway_C:** it already carries the highest calibrated baseline, so a degradation reads as a known-weaker gateway worsening — a more plausible narrative than the healthiest gateway suddenly failing. At 13% of traffic it is material without dominating, leaving four gateways as a strong control. Its response mix is already the most infrastructure-tilted (31.6% infrastructure-side attribution vs 23.3% for gateway_A), giving a natural axis to amplify.

**Secondary (harder, segment-scoped):** `gateway_C × SBI` — 8,185 transactions, 22.5/day, 6.27% baseline. Requires ≥7 days or a larger effect; forces RCA to isolate an intersection rather than a whole gateway.

**Not viable:** any sub-daily window at any cohort depth. At 89.8 transactions/day, gateway_C sees ~3.7/hour — inherited from the Day 1 temporal-density finding, not a Day 2B limitation.

---

## 7. Required incident inputs

Day 2C's injection should be parameterised by, at minimum:

```
incident_id
affected_gateway_id          -> synthetic_gateways.gateway_id
affected_segment             -> jsonb, e.g. {"sender_bank": ["SBI"]} or null for whole-gateway
incident_start / incident_end -> timestamptz, aligned to health-window bounds
incident_type                -> e.g. gateway_degradation
failure_multiplier           -> calibrated to reach the target rate
latency_multiplier
timeout_multiplier
ground_truth_root_cause      -> text, EVALUATION ONLY
generation_run_id            -> the run that materialised the incident
```

Calibration guidance: the reference dataset measured isolated rail degradations at **8–15× baseline over ~1 hour** ([DAY2B_CALIBRATION_SPEC.md](DAY2B_CALIBRATION_SPEC.md) §8). For a 3-day window at gateway_C, a multiplier of ~3.1–3.9× reaches 20–25%.

## 8. Required synthetic output behaviour

An injected incident must produce, **together and from one state change**:

1. elevated failure rate in the affected cohort,
2. elevated latency (more `ELEVATED`/`TIMEOUT` regime rows),
3. a response mix shifted toward `infrastructure_side` (`PROCESSING_ERROR`, `TIMEOUT`),
4. `gateway_health_state = 'DEGRADED'` on affected assignments,
5. **no change** to control-group gateways,
6. a different generation fingerprint from the baseline run,
7. full lineage: every affected row still traces to generation run → ingestion run → source SHA-256.

Point 5 is what Approach A cannot deliver.

---

## 9. Observed / synthetic boundary

Unchanged from Day 2B and verified live:

| Layer | Where | Enters diagnosis? |
|---|---|---|
| Observed fact | `transactions` (16 columns) | Yes — evidence base |
| Synthetic infrastructure state | `synthetic_*` tables | Yes, always labelled |
| Synthetic observed signal | `synthetic_infrastructure_assignments` | Yes, labelled |
| **Incident ground truth** | Day 2C tables | **No — evaluation only** |
| Agent conclusion | Day 2D+ | Output, never input |

Read surface `v_transaction_infrastructure`: 14 `observed_*` columns, 13 `synthetic_*` columns, plus `transaction_provenance='OBSERVED'`, `infrastructure_provenance='SYNTHETIC'`, `infrastructure_is_synthetic`. Day 2C must extend this pattern — any simulated incident outcome needs its own unambiguous prefix (e.g. `simulated_*`) distinct from both `observed_*` and `synthetic_*`.

---

## 10. Schema changes required before Day 2C

**None to existing tables.** The health-state table already accepts time-bounded degradation with all three multipliers, and `DEGRADED`/`UNAVAILABLE` are already in the CHECK vocabulary.

Day 2C will need **new** tables for incidents and evidence, plus — if Approach B is adopted, as recommended — a column or table for the simulated incident-period outcome. Neither requires altering Day 2A or Day 2B structures.
