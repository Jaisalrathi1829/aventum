# Aventum

Aventum is an AI Payment Incident Intelligence Agent that detects, diagnoses, simulates, and safely resolves payment disruptions. It combines evidence-backed root-cause analysis with human-approved recovery actions to keep payment flows healthy.

## Current status

| Phase | State |
|---|---|
| Day 1 — dataset audit, feasibility, canonical data design | Complete (see [`docs/`](docs/)) |
| Day 1.5 — routing dataset integration audit | Complete ([`docs/ROUTING_DATASET_DECISION.md`](docs/ROUTING_DATASET_DECISION.md)) |
| Day 2A — canonical transaction ingestion pipeline | Complete ([`docs/DAY2A_INGESTION_REPORT.md`](docs/DAY2A_INGESTION_REPORT.md)) |
| Day 2A review + P1 provenance fix | Complete ([`docs/DAY2A_P1_FIX_REPORT.md`](docs/DAY2A_P1_FIX_REPORT.md)) |
| Day 2B — synthetic infrastructure baseline | Complete ([`docs/DAY2B_INFRASTRUCTURE_REPORT.md`](docs/DAY2B_INFRASTRUCTURE_REPORT.md)) |
| **Day 3 — incident injection, detection, evidence, RCA** | **Complete** ([`docs/DAY3_IMPLEMENTATION_REPORT.md`](docs/DAY3_IMPLEMENTATION_REPORT.md)) |
| Day 4 — counterfactual simulator, Qwen agent, recommendation, approval | Not started |
| Day 5 — frontend, end-to-end integration, verification, audit trail | Not started |

Implemented so far: canonical transaction ingestion, an explicitly synthetic payment-infrastructure baseline (gateways, routing, latency, response codes, health), and incident intelligence — controlled incident injection, an Approach B simulated-outcome layer, deterministic anomaly detection, an evidence engine, competing-hypothesis ranking, and explainable RCA. There is no counterfactual simulator, no agent, no recommendation or approval workflow, and no frontend.

---

## Day 2A — reproducing the canonical ingestion

### Prerequisites

- **Docker** (for the PostgreSQL instance) — or an existing PostgreSQL 14+ server
- **Python 3.12+** (developed and pinned against 3.14)
- The raw source at `data/raw/UPI Transactions 2024 Dataset/upi_transactions_2024.csv`
  (SHA-256 `8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6`). It is read-only; the pipeline never writes to `data/raw/`.

### 1. Start the database

```bash
cd backend && docker compose up -d
```

Starts `aventum-postgres` on host port **5433** (deliberately not 5432, so it cannot collide with an existing local PostgreSQL).

### 2. Create the Python environment

```bash
cd backend && python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
```

On macOS/Linux use `.venv/bin/python` in place of `.venv/Scripts/python` throughout.

### 3. Environment variables

Both are optional — the defaults match the compose file and the repo layout.

| Variable | Default | Purpose |
|---|---|---|
| `AVENTUM_DATABASE_URL` | `postgresql+psycopg://aventum:aventum_local_dev@localhost:5433/aventum` | Target database |
| `AVENTUM_SOURCE_PATH` | `data/raw/UPI Transactions 2024 Dataset/upi_transactions_2024.csv` | Source file override |

They can also be set in `backend/.env`. The compose credential is a throwaway local-development value, not a secret.

### 4. Apply migrations

```bash
cd backend && .venv/Scripts/python -m alembic upgrade head
```

Creates `banks` (seeded with the 8 audited banks), `transactions`, `transactions_staging`, `ingestion_runs`, `ingestion_rejects`, `dataset_registry` (seeded with the canonical dataset's verified identity), and the `v_transactions_canonical` view. `alembic downgrade base` returns to a clean state.

### 5. Run the ingestion

```bash
cd backend && .venv/Scripts/python -m aventum_ingest.cli ingest
```

Expected: 250,000 rows read, 250,000 valid, 0 rejected, 250,000 inserted, all 21 verification checks passing, canonical fingerprint `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8`.

**Dataset identity.** A file's `source_dataset` is resolved from its SHA-256 via `dataset_registry`, never from its filename. A file whose hash is not registered is refused before anything is written (exit code 5), so an unrelated file can neither be labelled as, nor overwrite, the canonical dataset. To load a genuinely new dataset, register it first — registration records identity only and never loads or replaces data:

```bash
cd backend && .venv/Scripts/python -m aventum_ingest.cli register --source <path> --name <dataset_name>
```

`... cli datasets` lists registered identities.

Re-running is safe: an identical source is detected by SHA-256 and skipped (`SKIPPED_IDEMPOTENT`) without changing anything. Add `--force` to re-execute deliberately; it converges on the same fingerprint rather than duplicating rows.

### 6. Verify

```bash
cd backend && .venv/Scripts/python -m aventum_ingest.cli verify
```

Re-runs every post-load check against the live database and reprints the fingerprint. `... cli status` lists recent ingestion runs.

---

## Day 2B — generating the synthetic infrastructure baseline

Attaches an explicitly synthetic infrastructure layer (gateway, routing decision, latency, response code, health) to the canonical transactions. It never writes to `transactions`.

```bash
cd backend && .venv/Scripts/python -m aventum_synth.cli generate
```

Expected: 250,000 assignments, all gateways `HEALTHY`, generation fingerprint `e8414edd5a58c6cf04876e1bf48ca9a5564cf8d77da8eca4201c1732f52fe3c8`. Re-running with the same seed reproduces it exactly; `--seed` changes it. Add `--measure-memory` to report peak heap.

```bash
cd backend && .venv/Scripts/python -m aventum_synth.cli verify    # 23 checks + staleness
cd backend && .venv/Scripts/python -m aventum_synth.cli cohorts   # baseline cohort volumes
cd backend && .venv/Scripts/python -m aventum_synth.cli status    # generation run history
```

**Everything this layer produces is synthetic.** Read it through `v_transaction_infrastructure`, which prefixes every column `observed_*` or `synthetic_*`. See [`docs/DAY2B_TRUTH_MODEL.md`](docs/DAY2B_TRUTH_MODEL.md) for what may and may not be claimed about it.

---

## Day 3 — incident intelligence

Injects a controlled, explicitly synthetic incident and runs the full diagnosis chain:
`inject → simulate → detect → evidence → hypotheses → RCA`.

```bash
cd backend && .venv/Scripts/python -m aventum_incident.cli scenarios
```

Runs all three scenarios. `golden` is the flagship gateway_C degradation (RCA should name gateway_C); `quiet` scans an ordinary window and must stay silent; `alternative` injects an issuer-centred degradation and must **not** blame a gateway — the check that the engine reasons from evidence rather than a memorised answer.

```bash
cd backend && .venv/Scripts/python -m aventum_incident.cli status
cd backend && .venv/Scripts/python -m aventum_incident.cli handoff <analysis_run_id>   # Day 4 object as JSON
```

**`transactions` is never written to.** An incident *adds* modelled failures in a separate `simulated_incident_outcomes` layer; it never reallocates observed ones, and the database enforces this with a CHECK constraint. Incident ground truth lives in its own table that no detection or RCA code path reads. See [`docs/DAY3_IMPLEMENTATION_REPORT.md`](docs/DAY3_IMPLEMENTATION_REPORT.md).

### Run the tests

```bash
cd backend && .venv/Scripts/python -m pytest
```

362 tests covering normalization, validation, database constraints, dataset-identity provenance, pipeline behaviour (atomicity, idempotency, drift, quarantine, failure recovery), synthetic-infrastructure determinism/provenance/coherence/distribution, incident injection and Approach B, detection and alert discipline, evidence traceability, hypothesis ranking, RCA with adversarial ground-truth isolation, and full regression runs against the real 250K source. Database tests use a separate `aventum_test` database and never touch the canonical load.

---

## Layout

```text
aventum/
├── backend/             # ingestion + synthetic infrastructure + incidents, migrations, tests
│   ├── aventum_ingest/  # Day 2A canonical ingestion package
│   ├── aventum_synth/   # Day 2B synthetic infrastructure package
│   ├── aventum_incident/# Day 3 incident injection, detection, evidence, RCA
│   ├── migrations/      # Alembic
│   └── tests/
├── data/
│   ├── raw/            # source datasets (read-only, never modified)
│   ├── processed/
│   └── metadata/
├── docs/               # Day 1 / 1.5 / 2A / 2B documentation (source of truth)
├── audit_scripts/      # Day 1 profiling scripts, separate from production code
├── frontend/           # not started
├── simulator/          # not started
└── agent/              # not started
```

`docs/AVENTUM_CANONICAL_SCHEMA.md`, `docs/DATABASE_DESIGN.md`, and `docs/DATA_DICTIONARY.md` are the authoritative data-architecture documents; the ingestion pipeline implements them, and any deviation is recorded in `docs/DAY2A_INGESTION_REPORT.md`. Day 2B's synthetic layer is governed by `docs/DAY2B_TRUTH_MODEL.md` and `docs/DAY2B_CALIBRATION_SPEC.md`.
