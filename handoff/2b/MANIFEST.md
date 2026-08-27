# Day 2B Handoff — Synthetic Payment Infrastructure Baseline

Snapshot of every file created or modified during **Day 2B** of Project Aventum. Paths mirror their location in the real repo (`aventum/…`), so nothing is ambiguous where filenames repeat across packages (`models.py`, `cli.py`, and `verify.py` exist in both `aventum_ingest/` and `aventum_synth/`).

This is a **copy for reading/sharing**. The live code is at the same relative paths under `aventum/`.

---

## What Day 2B did

Attached an explicitly **synthetic** payment-infrastructure layer (gateway identity, routing decision, latency, response/error code, gateway health) to the 250,000 canonical observed UPI transactions produced by Day 2A — without modifying a single canonical row.

The baseline represents **normal operation**. No incident is injected, and there is no anomaly detection, RCA, simulation, recommendation, or agent logic (all Day 2C+).

Key results:

| | |
|---|---|
| Assignments generated | 250,000 (100% coverage) |
| Gateways | 5 (`gateway_A`…`gateway_E`), failure rates 3.94%–6.42% (1.54× spread) |
| Health | 100% `HEALTHY` — no degradation injected |
| Generation fingerprint | `e8414edd5a58c6cf04876e1bf48ca9a5564cf8d77da8eca4201c1732f52fe3c8` |
| Performance | 24.03 s · 10,404 rows/sec · 36.4 MB peak heap |
| Tests | 260 passed (198 Day 2A regression + 62 new) |

---

## Files in this snapshot

### New — synthetic infrastructure package (`backend/aventum_synth/`)

| File | What it does |
|---|---|
| `__init__.py` | Package docstring, model/config version constants, binding non-claims |
| `calibration.py` | Calibration transfer from the Nigerian reference dataset. Reference measurements and derived Aventum parameters kept in separate namespaces |
| `rng.py` | Deterministic PRNG: one SHA-256 per transaction sliced into 4 disjoint 64-bit lanes. Explicitly avoids Python's salted `hash()` |
| `models.py` | SQLAlchemy models for the 6 synthetic tables, incl. all `CHECK` constraints |
| `outcome_model.py` | Generative model: `GatewayRuntimeProfile` + coherent `response → regime → latency` chain |
| `routing.py` | Routing policy, eligibility, and the **status-conditioned** gateway selection |
| `generator.py` | Orchestration: config seeding, streamed generation, batched COPY, fingerprint |
| `verify.py` | Post-generation verification (4σ distribution bounds), staleness assessment, cohort analysis |
| `cli.py` | `generate` / `verify` / `status` / `cohorts` commands |

### New — schema and tests

| File | What it does |
|---|---|
| `backend/migrations/versions/0003_synthetic_infrastructure.py` | Creates the 6 synthetic tables + `v_transaction_infrastructure` read surface |
| `backend/tests/test_synthetic_infrastructure.py` | 62 tests: DB integrity, determinism, provenance, coherence, distribution, staleness, cohorts |

### New — documentation

| File | What it does |
|---|---|
| `docs/DAY2B_INFRASTRUCTURE_REPORT.md` | Main engineering report — architecture, results, performance, limitations, Day 2C requirements |
| `docs/DAY2B_CALIBRATION_SPEC.md` | Parameter-by-parameter calibration transfer, incl. what was deliberately *not* transferred |
| `docs/DAY2B_TRUTH_MODEL.md` | Epistemic layering: observed vs synthetic vs ground truth vs agent conclusion |
| `docs/DAY2B_DEMO_READINESS.md` | Flagship incident cohort analysis with detectability math |

### Modified

| File | Change |
|---|---|
| `backend/migrations/env.py` | Imports `aventum_synth.models` so Alembic sees the new tables |
| `backend/tests/conftest.py` | Truncates synthetic tables between tests; short pool timeout so a leaked connection fails fast instead of hanging |
| `README.md` | Day 2B status, generation commands, updated layout and test count |

---

## Three design decisions worth understanding first

**1. Status-conditioned gateway assignment** (`routing.py`, explained in `DAY2B_TRUTH_MODEL.md`)

`transactions.status` is observed fact and read-only, so Day 2B cannot generate outcomes. But assigning gateways independently of status would give every gateway the same ~4.95% failure rate, leaving the calibrated differentiation purely notional. So selection draws from the posterior `P(gateway | status)` instead of the prior. This preserves observed marginals *exactly* while producing genuinely differentiated per-gateway rates.

It **attributes** observed outcomes to synthetic gateways in calibrated proportions — it does not claim a gateway caused any failure.

**2. Health as time-bounded intervals, not a column** (`models.py`)

`synthetic_gateway_health_states` stores `(gateway, valid_from, valid_to, failure_multiplier, latency_multiplier, timeout_multiplier)`. Day 2B writes one `HEALTHY` window per gateway; Day 2C injects `DEGRADED` windows into the same table **without a migration**, and every downstream signal moves together through `GatewayRuntimeProfile`.

**3. Provenance is machine-enforced, not documented**

Four independent mechanisms: `synthetic_` table-name prefix · `is_synthetic NOT NULL CHECK (is_synthetic = true)` on all six tables · `observed_*` / `synthetic_*` column prefixes in the read surface · calibration provenance stored as data.

---

## Reading order suggestion

1. `docs/DAY2B_TRUTH_MODEL.md` — the epistemic rules everything else obeys
2. `docs/DAY2B_INFRASTRUCTURE_REPORT.md` — what was built and what it produced
3. `backend/aventum_synth/calibration.py` → `routing.py` → `outcome_model.py` → `generator.py` — the model, in dependency order
4. `backend/migrations/versions/0003_synthetic_infrastructure.py` — the schema and its constraints
5. `docs/DAY2B_DEMO_READINESS.md` — what Day 2C can build on

---

## Context this snapshot does not include

Day 2B builds on Day 2A (canonical ingestion of `upi_transactions_2024`, 250,000 rows, SHA-256 `8e46a45f…c89b6`) and its P1 provenance fix. Those files live under `aventum/backend/aventum_ingest/` and `aventum/docs/DAY2A_*.md`. The Nigerian routing dataset is a **synthetic calibration reference only** and is never joined to UPI transactions — see `aventum/docs/ROUTING_DATASET_DECISION.md`.
