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
| **Day 4A — counterfactual simulator, policy gate, recommendation, approval, simulated execution** | **Complete** ([`docs/DAY4A_IMPLEMENTATION_REPORT.md`](docs/DAY4A_IMPLEMENTATION_REPORT.md)) |
| **Day 4B — Qwen agent, typed tools, orchestration** | **Complete** ([`docs/DAY4B_IMPLEMENTATION_REPORT.md`](docs/DAY4B_IMPLEMENTATION_REPORT.md), [P1 fix](docs/DAY4B_P1_FIX_REPORT.md)) |
| **Day 5 — API, frontend, verification, batch measurement, audit** | **Complete** ([`docs/DAY5_IMPLEMENTATION_REPORT.md`](docs/DAY5_IMPLEMENTATION_REPORT.md)) |

Aventum is now end to end: canonical transaction ingestion, an explicitly synthetic payment-infrastructure baseline, incident intelligence (controlled injection, Approach B simulated outcomes, deterministic detection, an evidence engine, competing-hypothesis ranking, explainable RCA), the Day 4A deterministic decision core (counterfactual simulation, business impact, a 13-gate fail-closed policy, bounded recommendations, human approval, simulated execution, append-only audit), a Day 4B Qwen3 8B agent that interprets and orchestrates but computes nothing, and a Day 5 product surface — an HTTP API, a React operations console, independent post-action verification, and batch recovery measurement.

The authority chain is the point: **deterministic systems calculate → the agent interprets → policy constrains → a human approves → a simulated adapter acts → independent verification measures.** Every layer can refuse, and verification can conclude that an action did not help.

Everything is a synthetic incident with simulated execution. No production infrastructure is contacted and no recovery of real money is claimed.

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

---

## Day 4A — the deterministic decision core

Consumes a Day 3 diagnosis and runs the full decision spine — **with no LLM involved at all**:
`counterfactual simulation → business impact → NO_ACTION comparison → policy gate → recommendation → human approval → simulated execution → audit`.

```bash
cd backend && .venv/Scripts/python -m aventum_action.cli decide <analysis_run_id>
```

Simulates 13 candidates (NO_ACTION plus 10/20/30% reroutes to each eligible healthy gateway), selects deterministically, and validates against 13 fail-closed policy gates. On the flagship incident it selects `gateway_C → gateway_A @ 30%` for a projected **19,126.26 INR retained** and passes every gate.

Approval is a **separate, human step** — there is deliberately no `--auto-approve` flag:

```bash
cd backend && .venv/Scripts/python -m aventum_action.cli request <recommendation_id>
cd backend && .venv/Scripts/python -m aventum_action.cli approve <approval_id> --approver <name>
cd backend && .venv/Scripts/python -m aventum_action.cli execute <recommendation_id> <approval_id>
cd backend && .venv/Scripts/python -m aventum_action.cli verify <action_id>   # Day 5 handoff as JSON
cd backend && .venv/Scripts/python -m aventum_action.cli audit <incident_id>  # append-only trail
```

**Three properties are structural, not procedural.** The recommendation builder accepts *no numeric parameter* — every figure is read server-side from the persisted simulation, so a fabricated number has no way in. `SHA256(recommendation_id ‖ approval_id ‖ adapter_name)` is UNIQUE on `actions`, so concurrent executions produce exactly one adapter call. And the input fingerprint is re-derived from the live world at execution time, so a stale action cannot run.

**Execution is simulated.** `actions.is_simulated` carries `CHECK (= true)` — the database refuses to record a Day 4 execution as real. GMV figures use observed amounts with modelled outcomes, so outputs say *projected GMV retained*, never "recovered GMV". Capacity is reported `UNAVAILABLE` because no capacity telemetry exists, and no recovery claim is made — Day 5 owns verification. See [`docs/DAY4A_IMPLEMENTATION_REPORT.md`](docs/DAY4A_IMPLEMENTATION_REPORT.md).

## Day 5 — the product: API, console, verification

Day 5 turns the spine into one application. It adds the HTTP API (the browser's only surface), the React operations console, **independent post-action verification**, and **batch recovery measurement**.

### Run the whole thing

```bash
# 1. Database
cd backend && docker compose up -d

# 2. Python environment and migrations  (head 0007)
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m alembic upgrade head

# 3. API  ->  http://localhost:8000
.venv/Scripts/python -m uvicorn aventum_api.app:app --port 8000

# 4. Console  ->  http://localhost:5173   (separate terminal)
cd "../razorpay frontend new"
npm install
npm run dev
```

**Optional — the agent.** Everything above works without it. Deterministic analysis, simulation, policy, approval, execution and verification are unaffected when it is down, and the console says so rather than fabricating an explanation.

```bash
ollama serve
ollama pull qwen3:8b
```

### Configuration

`backend/.env.example` and `razorpay frontend new/.env.example` document every variable. The defaults work with no configuration; `.env*` is gitignored and no secret is committed.

| Variable | Default | Purpose |
|---|---|---|
| `AVENTUM_DATABASE_URL` | local compose | Database connection |
| `AVENTUM_CORS_ORIGINS` | dev + preview | Closed origin list, never `*` |
| `AVENTUM_AGENT_ENABLED` | `1` | Set `0` to disable agent endpoints |
| `AVENTUM_DEMO_RESET` | `1` | Set `0` to disable demo reset |
| `VITE_AVENTUM_API_URL` | `http://localhost:8000` | The only backend the console knows |

### The flagship demo

Overview → open the `gateway_C` incident → evidence and RCA → bounded simulations → recommendation and policy gates → request approval → approve as a human → simulated execution → independent verification → audit trail → batch impact.

`POST /api/demo/reset` restores a clean starting state. It truncates an explicit allow-list of workflow tables and **cannot** touch observed transactions, the synthetic baseline, or Day 3 analysis — no manual SQL, no fixture editing.

### Verification, and why it is independent

Day 4A deliberately makes no recovery claim; Day 5 answers the question. Verification uses **its own thresholds** (importing nothing from the policy layer), measures the adapter's post-action population against the execution-time baseline, runs seven integrity checks that re-walk lineage and recompute the execution fingerprint — and **can return `RECOVERY_NOT_VERIFIED`**, including when the raw movement was positive but the projection was badly missed.

Flagship result: failure rate **20.83% → 17.42%**, 79 transactions moved, **₹19,126.26 projected** versus **₹14,668.00 actually recovered**. Those two figures are different quantities and are never summed or substituted.

---

### Run the tests

```bash
cd backend && .venv/Scripts/python -m pytest
```

602 tests covering normalization, validation, database constraints, dataset-identity provenance, pipeline behaviour (atomicity, idempotency, drift, quarantine, failure recovery), synthetic-infrastructure determinism/provenance/coherence/distribution, incident injection and Approach B, detection and alert discipline, evidence traceability, hypothesis ranking, RCA with adversarial ground-truth isolation, full regression runs against the real 250K source, and the Day 4A decision core (counterfactual validity, probability consistency with Day 3, deterministic risk, every policy gate failing closed independently, recommendation-number provenance, approval integrity, execution revalidation, and real-thread concurrency), the Day 4B agent layer (typed tools, closed dispatch, prompt-injection defence, budget enforcement, ground-truth isolation), and the Day 5 product surface — verification's negative outcomes, batch measurement honesty, demo-reset containment, and an API red-team weighted toward refusals (forged state, injected numerics, duplicate approval/execution/verification, policy bypass, credential exposure). Database tests use a separate `aventum_test` database and never touch the canonical load.

---

## Layout

```text
aventum/
├── backend/             # ingestion + synthetic infrastructure + incidents, migrations, tests
│   ├── aventum_ingest/  # Day 2A canonical ingestion package
│   ├── aventum_synth/   # Day 2B synthetic infrastructure package
│   ├── aventum_incident/# Day 3 incident injection, detection, evidence, RCA
│   ├── aventum_counterfactual/ # Day 4A simulator, impact, risk, optimization
│   ├── aventum_policy/  # Day 4A deterministic safety gate
│   ├── aventum_action/  # Day 4A recommendation, approval, execution, audit
│   ├── aventum_agent/   # Day 4B Qwen agent: typed tools, loop, evaluation
│   ├── aventum_verification/ # Day 5 independent verification + batch measurement
│   ├── aventum_api/     # Day 5 HTTP API — the browser's only surface
│   ├── migrations/      # Alembic
│   └── tests/
├── data/
│   ├── raw/            # source datasets (read-only, never modified)
│   ├── processed/
│   └── metadata/
├── docs/               # Day 1 / 1.5 / 2A / 2B documentation (source of truth)
├── audit_scripts/      # Day 1 profiling scripts, separate from production code
├── razorpay frontend new/  # Day 5 React operations console (Vite + Tailwind v4)
├── handoff/            # per-phase snapshots with manifests
├── frontend/           # superseded by `razorpay frontend new/`
├── simulator/          # not started
└── agent/              # not started
```

`docs/AVENTUM_CANONICAL_SCHEMA.md`, `docs/DATABASE_DESIGN.md`, and `docs/DATA_DICTIONARY.md` are the authoritative data-architecture documents; the ingestion pipeline implements them, and any deviation is recorded in `docs/DAY2A_INGESTION_REPORT.md`. Day 2B's synthetic layer is governed by `docs/DAY2B_TRUTH_MODEL.md` and `docs/DAY2B_CALIBRATION_SPEC.md`.
