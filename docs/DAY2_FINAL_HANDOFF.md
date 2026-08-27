_Aventum internal handoff — Day 2 closeout._

# Day 2 Final Handoff

Final release gate, architecture freeze, and handoff for Day 2 of Project Aventum's five-day compressed build. This document is the single entry point for anyone (human or agent) starting Day 3.

---

## Executive Summary

Day 2 built the canonical transaction pipeline (Day 2A) and an explicitly synthetic payment-infrastructure baseline (Day 2B) on top of it, both independently reviewed. The release gate run for this closeout re-confirmed every critical claim directly against the live database: 260/260 tests pass, both fingerprints are unchanged, provenance is machine-enforced with zero violations, lineage is unbroken, and no Day 3+ artifact exists yet.

Day 2B's independent review found one architectural decision that must be locked before Day 3 writes any code: incidents must be built via a separate simulated-outcome layer (**Approach B**), never by reallocating observed historical failures (**Approach A**), because reallocation was measured to make the healthy control group appear up to 2× healthier during an incident — an artifact that would bias RCA evaluation optimistically. That decision is locked below and is binding on Day 3.

**Final status: DAY 2 COMPLETE — READY FOR DAY 3.**

---

## Day 2 Deliverables

| Deliverable | Status |
|---|---|
| Canonical ingestion pipeline (`aventum_ingest`) | Complete |
| `transactions` table, 250,000 rows | Complete |
| Day 2A architecture review | Complete — 1 P1 found (provenance) |
| Day 2A P1 fix (SHA-256-backed dataset registry) | Complete |
| Synthetic infrastructure package (`aventum_synth`) | Complete |
| 6 synthetic tables + `v_transaction_infrastructure` view | Complete |
| Day 2B architecture review | Complete — 0 P0, 1 P1 (architectural, for Day 3), 6 P2 |
| Day 2 final release gate (this closeout) | Complete |
| `docs/DAY3_IMPLEMENTATION_CONTRACT.md` | Complete |
| `docs/5_DAY_EXECUTION_PLAN.md` | Complete |

---

## Verified Metrics

Re-verified directly against the live database during this closeout, not copied from prior reports.

| Metric | Value |
|---|---|
| Full test suite | **260 / 260 passed**, 0 failed, 0 skipped (159.03 s) |
| Canonical rows | 250,000 |
| Canonical fingerprint | `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8` — **unchanged** |
| Synthetic assignment rows | 250,000 |
| Generation fingerprint | `e8414edd5a58c6cf04876e1bf48ca9a5564cf8d77da8eca4201c1732f52fe3c8` — **unchanged** |
| Staleness state | `CURRENT` |
| `is_synthetic = false` rows, across all 7 synthetic tables | **0** |
| Broken lineage rows (assignment → transaction / ingestion run / generation run) | **0** |
| `source_ingestion_run_id` mismatches (assignment vs. transaction) | **0** |
| Day 3+ tables present (`incidents`, `simulations`, `recommendations`, `actions`, etc.) | **0 — none exist** |
| Frontend / simulator / agent directories | Empty |

---

## Final Architecture

```
data/raw/upi_transactions_2024.csv  (SHA-256 8e46a45f...c89b6)
        │  Day 2A: aventum_ingest
        ▼
transactions  (250,000 rows, canonical, OBSERVED, immutable)
        │  Day 2B: aventum_synth
        ▼
synthetic_gateways / synthetic_gateway_profiles / synthetic_routing_policies
synthetic_gateway_health_states / synthetic_generation_runs
        │
        ▼
synthetic_infrastructure_assignments  (250,000 rows, SYNTHETIC)
        │
        ▼
v_transaction_infrastructure  (observed_* / synthetic_* labelled read surface)
```

Nigerian Card Payment Dataset for Predictive Routing: consulted only as calibration evidence at design time (`aventum_synth/calibration.py`). No row of it is imported, no table of it exists in the database, and it is never joined to `transactions`. Verified absent from `pg_tables`.

---

## Observed vs Synthetic Boundary

Unchanged since Day 2B, re-verified live:

| Layer | Lives in | Provenance mechanism |
|---|---|---|
| Observed fact | `transactions` (16 columns, all Day 2A) | No synthetic column exists on it (verified) |
| Synthetic infrastructure state/signal | 7 `synthetic_*` tables | `is_synthetic boolean NOT NULL CHECK (is_synthetic = true)` on all 7 — rejects `false` at the database level |
| Read surface | `v_transaction_infrastructure` | Every column prefixed `observed_*` or `synthetic_*`, plus explicit `transaction_provenance` / `infrastructure_provenance` markers |
| Incident ground truth (Day 3+) | not yet built | must be evaluation-only — see Non-Negotiable Rules below |

Full epistemic model: `docs/DAY2B_TRUTH_MODEL.md`.

---

## Locked Decisions

1. **Approach B is locked for Day 3** (see next section) — not open for reconsideration without new evidence.
2. **The golden incident scenario is locked**: gateway_C, 3-day window, 20–25% degraded rate, control = gateway_A/B/D/E, expected signal ~9–13σ. Backed by measured detectability math in `docs/DAY2B_ARCHITECTURE_REVIEW.md` §Flagship Cohort Readiness.
3. **`baseline-v1` gateway profiles and routing policy are frozen.** Day 3 may add new versions; it may not mutate the existing ones.
4. **No Day 2 P2 item is being fixed now** — see Deferred Technical Debt.
5. **The five-day plan (`docs/5_DAY_EXECUTION_PLAN.md`) is the scope authority** for Days 3–5; this handoff does not duplicate it.

---

## Approach B Requirement

```
Observed historical outcome   ≠   Simulated incident outcome
```

`transactions.status` — and every other observed field — remains immutable through Day 3, Day 4, Day 5, and beyond. Incident-period behavior is represented by a **new, separately-provenanced simulated-outcome layer** that adds modeled failures to the affected cohort. It never redistributes the 12,376 real observed failures away from healthy gateways.

**Measured reason this is locked, not a preference:** on a real 3-day window (2024-06-01→04 IST, 2,093 transactions, 109 observed failures), reallocating failures to push gateway_C to a 25% failure rate required moving 47 failures away from the other four gateways, dropping their combined rate from 4.92% to 2.35% — **0.48× baseline**. No real gateway degradation makes its peers healthier. That artifact would hand Day 3's anomaly detector and RCA an artificially easy, non-representative contrast, and would cap how severe an incident could even be simulated (the window runs out of failures to move well before a realistic outage magnitude). Full derivation: `docs/DAY2B_ARCHITECTURE_REVIEW.md` §Status-Conditioned Attribution Model, part D, and `docs/DAY2C_INTERFACE_READINESS.md` §5.

---

## Day 3 Contract

Full contract: **`docs/DAY3_IMPLEMENTATION_CONTRACT.md`**. Summary of what it fixes:

- Objective: inject incident → generate simulated outcomes → detect anomaly → collect evidence → produce explainable RCA. Nothing more.
- Golden scenario locked (above).
- Tables to create: `incidents`, `simulated_incident_outcomes`, `incident_evidence`, `incident_evaluation` (naming may follow existing conventions; concepts and provenance rules are fixed).
- Five output interfaces fixed for Day 4 consumption: Incident, Simulated outcome, Detection, RCA evidence, RCA result (exact fields in the contract doc).
- Extension point: `synthetic_gateway_health_states` accepts a `DEGRADED` window for gateway_C with no migration.
- Acceptance gate: 8 conditions, including the hard requirement that `incidents.ground_truth_root_cause` is never referenced by the detection/RCA code path.

---

## Day 4 Inputs

Day 4 (counterfactual simulation + Qwen agent + bounded recommendation + human approval) consumes exactly the five Day 3 output interfaces, unchanged:

- **Incident:** `incident_id · affected_gateway · affected_segment · incident_type · start · end · severity · ground_truth_root_cause · evaluation_only_flag/provenance`
- **Simulated outcome:** `transaction_id · observed_status · simulated_status · simulated_latency · simulated_response · incident_id · simulation_provenance`
- **Detection:** `anomaly_id · anomaly_score · detection_window · affected_population · baseline_metrics · current_metrics · significance/evidence_strength`
- **RCA evidence:** `evidence_id · metric · baseline · incident_value · delta · affected_gateway · affected_segment · control_group_comparison · evidence_source/provenance`
- **RCA result:** `suspected_root_cause · confidence · supporting_evidence_ids · alternatives_considered · explanation`

This handoff states what Day 4 receives, not how Day 4 implements counterfactual simulation, agent tooling, or recommendation logic — that design belongs to Day 4 planning.

---

## Day 5 Flagship Flow

Day 5 wires the full chain — Detect → Diagnose → Explain → Simulate → Recommend → Approve → (Execute) → Verify → Audit — into a real frontend for the golden incident scenario, with an end-to-end integration test, a verified post-action outcome, and a complete audit trail tying back to the same provenance chain established in Day 2 (source SHA-256 → ingestion run → generation run → incident → recommendation → approval → outcome). Full scope: `docs/5_DAY_EXECUTION_PLAN.md` §DAY 5.

---

## Deferred Technical Debt

All six Day 2B P2 items remain **open and deferred**. None blocks Day 3 correctness; none is being fixed in this closeout.

| # | Item | Why deferred |
|---|---|---|
| P2-1 | No failure ever lands in the NORMAL latency regime — latency perfectly (one-directionally) predicts success | Cosmetic realism gap, not a correctness issue; does not block Day 3's ability to inject or detect the golden incident |
| P2-2 | No regression test for the streaming/memory generation fix (844 MB → 36.4 MB) | Efficiency guard, not correctness; reverting would still pass all 260 tests |
| P2-3 | No compactness assertion on `eligible_gateways` JSON | Same category as P2-2 |
| P2-4 | ~41 MB of per-row constant redundancy in `synthetic_infrastructure_assignments` (~1.6 GB projected at 10M rows) | Not relevant at 250K-row demo scale |
| P2-5 | Response taxonomy defined in two live places (`calibration.py`, `models.py`) plus a frozen migration copy | DB rejects any mismatch immediately; low risk |
| P2-6 | `generator.py` at 737 lines mixes several responsibilities | Navigable as-is; natural split point if Day 3 extends it significantly |

**Reopen policy:** revisit an item only if it directly blocks Day 3 correctness (for example, if Day 3's incident injection needs the streaming path extended and P2-2's missing guard becomes a real risk). Otherwise leave every item exactly as found.

---

## Day 3 Non-Negotiable Design Rules

1. `transactions` is immutable.
2. Observed historical outcomes are never overwritten.
3. Simulated incident outcomes use explicit simulated provenance (their own prefix/flag, distinct from both `observed_*` and `synthetic_*`).
4. Incident ground truth never enters diagnosis, detection, or RCA — evaluation only.
5. Nigerian calibration data remains a parameter source only — never imported, never joined.
6. Day 2 `baseline-v1` configuration is never mutated.
7. New randomness uses the existing deterministic RNG architecture (`aventum_synth.rng`) — never Python's salted `hash()`, never an unseeded `random`/`numpy.random`.
8. Incident generation must be reproducible — same inputs, same simulated outcomes, same fingerprint.
9. Incident effects must move failure, latency, and response behavior together, through one state-change funnel — never mutate each signal independently.
10. Healthy control-group gateways must remain unaffected by a localized gateway incident.
11. RCA must rely on evidence, not ground truth.
12. Every RCA conclusion must be traceable to specific, queryable evidence.

---

## Five-Day Plan

Full plan: **`docs/5_DAY_EXECUTION_PLAN.md`**.

```
DAY 1  Data and architecture                                          COMPLETE
DAY 2  Canonical pipeline + synthetic infrastructure                  COMPLETE
DAY 3  Incident injection + simulated outcomes + detection + RCA      NEXT
DAY 4  Counterfactual simulation + Qwen agent + recommendation/approval
DAY 5  Frontend + end-to-end integration + verification + audit + demo
```

---

## Final Status

# DAY 2 COMPLETE — READY FOR DAY 3
