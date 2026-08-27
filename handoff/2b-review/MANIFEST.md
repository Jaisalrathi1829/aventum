# Day 2B Review Handoff — Independent Verification Gate

Snapshot of the two documents produced by the **Day 2B independent architecture review**. Paths mirror the real repo (`aventum/docs/…`).

This was a **review-only** task: no production code was modified. Both files are new; nothing else in the repo changed.

---

## Verdict

# APPROVED WITH REQUIRED FIXES

Day 2B is well-built and verified. **0 P0 · 1 P1 · 6 P2.**

The single P1 is architectural direction for Day 2C, not a defect in Day 2B.

---

## Files

| File | What it is |
|---|---|
| `docs/DAY2B_ARCHITECTURE_REVIEW.md` | The full review verdict — 21 sections, every claim backed by a live-database query, executed command, or code inspection |
| `docs/DAY2C_INTERFACE_READINESS.md` | Binding handoff spec for Day 2C: tables, keys, invariants, the recommended incident scenario, and the outcome-architecture decision |

---

## The one thing to read first

**P1-1 — Day 2C must adopt "Approach B" for incident injection.**

Day 2B assigns gateways from the posterior `P(gateway | observed status)` so that per-gateway failure rates differ while observed marginals stay exact. That construction is mathematically correct and verified.

But it fixes the *total* number of failures in any time window. So injecting an incident by **reallocating** observed failures onto a degrading gateway necessarily **removes** them from the healthy ones. Measured on a real 3-day window:

| Target rate for the degraded gateway | Control-group failure rate | vs baseline |
|---|---|---|
| 10% | 4.54% | 0.92× |
| 20% | 3.06% | 0.62× |
| **25%** | **2.35%** | **0.48×** |
| 30% | 1.64% | 0.33× |

The healthy gateways would appear **twice as healthy during the incident**. No real degradation does that. It inflates the contrast an RCA engine sees, so a passing RCA evaluation would overstate real capability — and it caps achievable severity (beyond ~35% the window runs out of failures).

**Recommendation:** keep `transactions` immutable, but generate incident-period outcomes in a Day 2C-owned synthetic layer with its own provenance prefix (e.g. `simulated_*`), so a degradation *adds* failures rather than *moving* them. Full comparison in `DAY2C_INTERFACE_READINESS.md` §5.

**No Day 2B code change is required for this.**

---

## What was independently verified (not taken from the Day 2B report)

| Check | Result |
|---|---|
| Full test suite | 260/260 passed, 0 skipped, 155.93 s |
| Generation fingerprint | `e8414edd…e3c8` reproduced by a fresh run |
| Canonical fingerprint | `12dec963…f4b8` unchanged |
| RNG determinism | Byte-identical across 5 processes incl. `PYTHONHASHSEED=random` |
| `is_synthetic = false` | Rejected by PostgreSQL on **all 7** synthetic tables (adversarial test) |
| Lineage integrity | 0 broken chains; every row traces to source SHA-256 |
| Staleness detection | Controlled mismatch detected; FK blocks even writing one |
| Marginal preservation | Observed failures 12,376 = sum over gateways 12,376, exactly |
| Calibration vs docs | Every documented value matches the code; Σ(w·p) = observed rate exactly |
| Impossible combinations | All 6 = 0 across 250,000 rows; allowed slow-success case = 9,395 |
| Peak memory | 36.8 MB measured (report claimed 36.4 MB) |
| N+1 queries | 0 database calls inside the per-row loop |
| `eligible_gateways` size | 65 bytes, 1 distinct value — the ~125 MB duplication is genuinely gone |

---

## P2 issues (all deferrable)

1. **No failure is ever in the NORMAL latency regime** — `latency_regime = NORMAL` implies SUCCESS with certainty, making latency and failure-rate detectors perfectly redundant. Real systems have fast declines.
2. No regression test for the streaming/memory fix — reverting it would keep all 260 tests green.
3. No compactness assertion on `eligible_gateways`.
4. ~41 MB of per-row constant redundancy (≈1.6 GB at 10M rows).
5. Response taxonomy expressed in two live places (`calibration.py` and `models.py`).
6. `generator.py` at 737 lines mixes five responsibilities.

---

## Context not in this snapshot

The code being reviewed is in `handoff/2b/` (or live at `aventum/backend/aventum_synth/`). Day 2A's canonical ingestion and its P1 provenance fix are at `aventum/backend/aventum_ingest/` and `aventum/docs/DAY2A_*.md`.
