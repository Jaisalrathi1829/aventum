_Aventum internal engineering report._

# Day 2B Infrastructure Report — Synthetic Payment Infrastructure Baseline

Result of implementing the synthetic payment-infrastructure baseline and generative model. Every figure is measured from an actual run.

**Non-claims (binding):** nothing here describes real Razorpay gateways, routing decisions, latencies, or error codes; none of it is observed UPI gateway telemetry; the Nigerian routing dataset is a synthetic calibration reference, not production data. See [DAY2B_TRUTH_MODEL.md](DAY2B_TRUTH_MODEL.md).

---

## Executive Summary

Day 2B attaches an explicitly synthetic infrastructure layer to the 250,000 canonical observed transactions from Day 2A, without modifying a single canonical row. Five synthetic gateways, one versioned routing policy, per-gateway behavioural profiles, and time-bounded health windows drive a coherent generative model producing latency, response code, and attribution for every transaction.

The baseline represents **normal operation**: all gateways `HEALTHY`, failure rates differentiated across a calibrated 1.54× band (3.94%–6.42%), timeouts at 0.13% of traffic. No incident is injected.

Reproducibility is exact. A clean rebuild — full migration chain dropped to `base`, re-ingested, regenerated — reproduces both the canonical fingerprint (`12dec963…f4b8`) and the generation fingerprint (`e8414edd…e3c8`) byte-for-byte. **260 tests pass** (198 Day 2A regression + 62 new).

## Synthetic Infrastructure Architecture

```text
canonical transactions (READ ONLY)
        │
        ▼
routing policy  ──── eligibility (data-driven) ────►  eligible gateway set
        │
        ▼
gateway selection  ── deterministic hash draw, status-conditioned posterior
        │
        ▼
gateway profile × health window  ──►  GatewayRuntimeProfile
        │                              (effective failure prob, latency mult, response mix)
        ▼
outcome-generation model
        │  response family → latency regime → latency value
        ▼
synthetic_infrastructure_assignments   (one row per transaction)
        │
        ▼
v_transaction_infrastructure  (observed_* / synthetic_* labelled read surface)
```

Six tables, created by migration `0003`:

| Table | Purpose | Rows |
|---|---|---|
| `synthetic_generation_runs` | Reproducibility/audit anchor | 1 per generation |
| `synthetic_gateways` | Gateway universe | 5 |
| `synthetic_gateway_profiles` | Versioned behavioural parameters | 5 |
| `synthetic_routing_policies` | Versioned policy definition | 1 |
| `synthetic_routing_policy_gateways` | Eligibility + weights | 5 |
| `synthetic_gateway_health_states` | Time-bounded health windows | 5 |
| `synthetic_infrastructure_assignments` | Per-transaction output | 250,000 |

No incident, simulation, recommendation, action, or verification table was created.

## Gateway Universe

Five Aventum **model entities**, informed by the calibration reference's five-rail structure. Behaviour is data-driven (`synthetic_gateway_profiles`), not hard-coded in application logic.

| Gateway | Traffic weight | Relative failure multiplier | Baseline failure probability | Latency multiplier | Calibration source |
|---|---|---|---|---|---|
| gateway_A | 0.26 | 0.814 | 4.020% | 0.96 | rail_A |
| gateway_B | 0.27 | 1.038 | 5.125% | 1.00 | rail_B |
| gateway_C | 0.13 | 1.257 | 6.208% | 1.08 | rail_C |
| gateway_D | 0.21 | 0.935 | 4.616% | 0.99 | rail_D |
| gateway_E | 0.13 | 1.161 | 5.732% | 1.05 | rail_E |

Spread: **1.54×** best-to-worst. Differentiated enough for per-gateway analysis, well short of anything resembling an outage.

## Routing Policy

`baseline-v1` — *"Aventum synthetic baseline routing policy v1"*. All five active gateways are eligible for all traffic; eligibility conditions are `NULL` (unconditional) but the column exists so Day 2C can scope gateways without a migration.

Explicitly **not** adaptive routing, and **not** a representation of any real processor's algorithm.

## Routing Decision Model

`selection_method = synthetic_deterministic_hash_weighted_status_conditioned`, recorded on every row so the mechanism is never ambiguous downstream.

Each assignment retains: `routing_policy_version`, `eligible_gateways` (compact ID list), `selected_gateway_id`, `selection_method`, `selection_seed`, `gateway_profile_version`. The full *reasoned* eligibility snapshot (weights + per-gateway reasons) is stored once on the generation run rather than duplicated across 250,000 rows — that duplication would have cost ~125 MB of byte-identical JSON.

Together these answer both contract questions: *why was this gateway eligible?* (policy + snapshot) and *which policy version selected it?* (`routing_policy_version`).

**Status-conditioned selection** is the central modelling decision and is documented in full in [DAY2B_TRUTH_MODEL.md](DAY2B_TRUTH_MODEL.md). In brief: the observed `status` is immutable, so gateways are drawn from `P(gateway | status)` rather than `P(gateway)`. This preserves observed marginals exactly while producing genuinely differentiated per-gateway failure rates. It attributes observed outcomes to synthetic gateways in calibrated proportions; it does not claim a gateway caused any failure.

## Generative Outcome Model

Fields are never drawn independently. The chain is `observed status → response family → latency regime → latency value`, so incoherent combinations are unreachable by construction — and the database enforces the same invariants independently.

`GatewayRuntimeProfile` is the single funnel through which health influences everything: `effective_failure_probability`, `effective_latency_multiplier`, and `effective_response_mix()`. A Day 2C degradation raises multipliers on a health window and failure rate, latency, and response mix all move together, with no generator changes.

## Health Model

States: `HEALTHY`, `DEGRADED`, `UNAVAILABLE`. Modelled as **time-bounded windows** (`valid_from`, `valid_to`) with `failure_multiplier`, `latency_multiplier`, `timeout_multiplier` — an interval, not a column, precisely so Day 2C can insert degradation without schema change.

Day 2B writes one `HEALTHY` window per gateway spanning the canonical period, all multipliers `1.0`. **All 250,000 assignments are `HEALTHY`** — no degradation injected.

Windows are half-open `[valid_from, valid_to)` with `valid_to` padded one second past the last transaction, so the final transaction falls inside its own window.

## Latency Model

Lognormal per regime (right-skewed, as real latency is), hard-clamped to each regime's band.

| Regime | n | min | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|
| NORMAL | 228,229 | 99.0 | 421.5 | 717.4 | 892.6 | 1,800.0 |
| ELEVATED | 21,449 | 277.4 | 868.7 | 1,428.5 | 1,748.2 | 1,990.0 |
| TIMEOUT | 322 | 2,000.0 | 3,344.0 | 4,985.1 | 6,228.5 | 6,846.6 |

Overall: p50 437.7 ms, p95 893.4 ms, p99 1,285.2 ms, mean 487.85 ms.

Timeouts are **0.129%** of all traffic — the low frequency a normal baseline requires. Clamping makes it structurally impossible for a NORMAL draw to reach timeout territory even at the distribution tails.

## Response / Error Model

Aventum's own taxonomy — **not** real production error codes:

| Response | Attribution | Count | % of all |
|---|---|---|---|
| `APPROVED` | approved | 237,624 | 95.050% |
| `INSUFFICIENT_FUNDS` | issuer_side | 3,060 | 1.224% |
| `PROCESSING_ERROR` | infrastructure_side | 3,038 | 1.215% |
| `ISSUER_DECLINED` | issuer_side | 3,026 | 1.210% |
| `DO_NOT_HONOR` | issuer_side | 2,930 | 1.172% |
| `TIMEOUT` | infrastructure_side | 322 | 0.129% |

By attribution: approved 95.050%, issuer_side 3.606%, infrastructure_side 1.344%.

`APPROVED` count equals the observed `SUCCESS` count **exactly** (237,624) — the synthetic layer does not perturb observed marginals.

The `issuer_side` / `infrastructure_side` split is what later makes RCA possible: a degraded gateway should fail *differently*, not merely more often.

## Calibration Transfer

Full parameter-by-parameter derivation, including what was deliberately **not** transferred, is in [DAY2B_CALIBRATION_SPEC.md](DAY2B_CALIBRATION_SPEC.md). Summary:

| Transfer type | Parameters |
|---|---|
| Direct | Response taxonomy; base failure-response split |
| Scaled | Latency regime medians/σ (structure and ratios kept, values re-chosen) |
| Bounded | Inter-gateway failure spread (λ=0.6 damping); per-gateway latency offsets |
| Conceptual template | Traffic weights; response attribution mapping |
| Not transferred | Absolute failure level (taken from **observed** data); isolated degradation pattern (deferred to Day 2C) |

No calibration-dataset row is imported or joined — verified by test.

## Provenance

Four independent mechanisms, all machine-enforced:

1. `synthetic_` table-name prefix on all six tables.
2. `is_synthetic boolean NOT NULL DEFAULT true CHECK (is_synthetic = true)` — setting it false is rejected by PostgreSQL (tested across all six tables).
3. `v_transaction_infrastructure` prefixes every column `observed_*` / `synthetic_*` and carries explicit `transaction_provenance` / `infrastructure_provenance` markers.
4. Calibration provenance stored as data (`calibration_source_rail`, `calibration_reference_name`, full `model_parameters`).

Row-level lineage: every assignment carries `transaction_id`, `source_ingestion_run_id`, and `generation_run_id`, so any synthetic value traces back through the generation run and the ingestion run to the source file's SHA-256.

## Generation Runs

`synthetic_generation_runs` records `source_ingestion_run_id`, `generation_seed`, `generation_config_version`, `synthetic_model_version`, `routing_policy_version`, calibration reference name/version, timing, `status`, `rows_generated`, `generation_fingerprint`, `observed_failure_rate`, full `model_parameters`, and a `distribution_report`.

Statuses: `RUNNING`, `SUCCEEDED`, `FAILED`, `SUPERSEDED`.

## Staleness / Rebuild Policy

**Chosen policy: cascade-and-regenerate, with explicit staleness detection.**

- `synthetic_infrastructure_assignments.transaction_id` is `ON DELETE CASCADE`. Day 2A's promotion deletes and re-inserts canonical rows wholesale, so a re-ingestion **wipes** the synthetic population rather than silently orphaning it. Day 2A's idempotency guarantee is preserved untouched.
- `source_ingestion_run_id` is stored redundantly on every assignment *and* on the run, so staleness is detectable by comparison, not only by absence.
- `assess_staleness()` reports `CURRENT`, `STALE_INGESTION_MISMATCH`, `STALE_INCOMPLETE_COVERAGE`, or `ABSENT`.
- A new generation marks any prior run for the same ingestion run `SUPERSEDED` and clears its assignments, so exactly one live population exists at a time while the audit history is retained.

Stale synthetic infrastructure is never silently reused. Verified by `test_reingestion_cascades_away_stale_assignments`.

## Observed vs Synthetic Boundary

`transactions` is untouched: no writes, no synthetic columns (asserted by test, which checks the live column list). All 16 observed fields remain read-only.

`v_transaction_infrastructure` is the intended read surface for future RCA tooling. A flat, unlabelled record is never produced by it.

## Flagship Demo Readiness

Full analysis in [DAY2B_DEMO_READINESS.md](DAY2B_DEMO_READINESS.md). Recommended cohort:

**`gateway_C`, 3-day window** — 32,691 baseline transactions (89.3/day), 6.421% baseline failure rate. A degradation to 20–25% over 3 days (~268 transactions) yields roughly **9–13σ** against baseline.

Secondary: `gateway_B × SBI` (16,893 transactions, 46.2/day) for a segment-scoped incident, needing ≥3 days.

Not viable: sub-daily windows at any cohort depth — the Day 1 temporal-density finding, not a Day 2B limitation.

## Generated Dataset Statistics

| Metric | Value |
|---|---|
| Transactions assigned | 250,000 (100% coverage) |
| Source ingestion run | 1 |
| Generation run | 3 (latest); run 1 after clean rebuild |
| Generation seed | `aventum-day2b-baseline-001` |
| Observed failure rate (from canonical data) | 4.9504% |
| Gateway traffic | A 65,145 · B 67,365 · C 32,691 · D 52,597 · E 32,202 |
| Gateway failure rates | A 3.937% · D 4.677% · B 5.157% · E 5.521% · C 6.421% |
| Latency regimes | NORMAL 228,229 · ELEVATED 21,449 · TIMEOUT 322 |
| Health states | HEALTHY 250,000 (100%) |

## Generation Fingerprint

```
e8414edd5a58c6cf04876e1bf48ca9a5564cf8d77da8eca4201c1732f52fe3c8
```

SHA-256 over the ordered synthetic population, depending on `source_ingestion_run_id`, `generation_config_version`, `generation_seed`, `synthetic_model_version`, and every assignment's content. Ordered by `transaction_id`, so it is independent of physical row order; computed server-side.

**Reproduced exactly** after a full clean rebuild (`downgrade base` → `upgrade head` → re-ingest → regenerate). Changing the seed changes it (tested).

Determinism mechanism: one `hashlib.sha256` per transaction over `transaction_id|source_ingestion_run_id|generation_config_version|generation_seed`, sliced into four disjoint 64-bit lanes (gateway, response, latency, reserved). Python's built-in `hash()` is deliberately **not** used — it is salted per process and would differ between runs. No wall-clock time, row order, or insertion order participates in generation; wall-clock appears only in audit metadata.

## Performance

| Metric | Value |
|---|---|
| Duration (250,000 rows) | **24.03 s** |
| Throughput | **10,404 rows/sec** |
| Peak Python heap | **36.4 MB** |
| Method | Streamed server-side cursor read → batched COPY (20,000-row batches) → single transaction |

No per-row queries, no per-row commits, no O(n²) work.

An earlier implementation buffered the entire COPY payload in memory and peaked at **843.9 MB**. Streaming the COPY in batches reduced that to 36.4 MB (**23× lower**) and cut duration from 39.4 s to 24.0 s, with an **identical fingerprint** — confirming the change affected storage, not semantics. Memory is now flat in dataset size, so the same path extends to millions of events without redesign.

## Tests

| Suite | Tests | Result |
|---|---|---|
| Day 2A regression (`test_normalize`, `test_validate`, `test_db_constraints`, `test_pipeline`, `test_regression_full_source`, `test_dataset_provenance`) | 198 | **passed** |
| Day 2B (`test_synthetic_infrastructure`) | 62 | **passed** |
| **Total** | **260** | **260 passed, 0 failed** (155.75 s) |

Day 2B coverage: database integrity (FKs for transaction/generation-run/gateway, uniqueness, `is_synthetic` enforcement across all six tables, canonical immutability); determinism (digest stability, input sensitivity, lane independence, regeneration fingerprint equality, seed sensitivity, assignment/latency/response stability); provenance (synthetic flag, lineage match, calibration recording, run metadata, no calibration rows imported); staleness (current/absent/cascade-on-reingestion/supersession); internal consistency (six impossible-combination tests, four of them asserted against the **database** rather than the generator); outcome-model unit behaviour (health→probability, health→response mix, status-conditioned selection bias); distribution bounds (4σ traffic share and failure rate per gateway, taxonomy, regime bands, baseline health); read surface; cohort volumes; and two full-scale 250K checks.

## Known Limitations

1. **Status-conditioned assignment constrains incident magnitude.** Per-gateway failure rates are bounded by the observed canonical outcomes in any window: raising one gateway's rate *redistributes* that window's observed failures rather than creating new ones. Workable for the recommended 3-day cohort, but Day 2C must design within this ceiling or deliberately decide that incident windows generate their own outcomes. Flagged in [DAY2B_DEMO_READINESS.md](DAY2B_DEMO_READINESS.md).
2. **Sub-daily incidents remain infeasible.** Inherited from the dataset's temporal density (Day 1 finding), not introduced here.
3. **Provenance safety depends on tool authors at the boundary.** The four enforcement mechanisms protect the intended read surface, but a future tool could still write a bespoke query that strips `synthetic_` prefixes before handing rows to an LLM. A Day 2C+ tool-design obligation, not a solved problem.
4. **Latency differentiation across gateways is an Aventum invention.** The calibration reference showed essentially no inter-rail latency spread (3.18 ms at p50). The ≤8% offsets are a documented modelling decision, not transferred evidence.
5. **Single-policy baseline.** Only `baseline-v1` exists, with unconditional eligibility. The schema supports versioned and conditional policies; none are exercised yet.
6. **Generation is single-threaded.** 24 s at 250K is comfortable; at ~10M rows this would be ~16 minutes. Batched COPY already supports parallelising by transaction-ID range if needed.

## Day 2C Requirements

1. Inject a `DEGRADED` health window for `gateway_C` (recommended: 3 days, failure multiplier calibrated to reach 20–25%), using the existing `synthetic_gateway_health_states` table — **no migration required**.
2. Resolve the incident-magnitude ceiling in Limitation 1 explicitly, and document the choice.
3. Create incident ground-truth tables, kept strictly out of the diagnosis path ([DAY2B_TRUTH_MODEL.md](DAY2B_TRUTH_MODEL.md)).
4. Regenerate the affected window so failure rate, latency, and response mix move together through `GatewayRuntimeProfile`.
5. Verify the injected incident is detectable at the predicted σ, and that healthy gateways remain within their baseline bounds (the 4σ checks already provide the control).
