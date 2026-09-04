# Aventum

**AI payment incident intelligence.** Aventum detects a payment disruption, diagnoses it
from evidence, simulates bounded recovery options, asks a human to approve one, executes
it through a simulated adapter, then independently measures whether it actually helped —
including concluding that it did not.

The authority chain is the point:

> **deterministic systems calculate → the agent interprets → policy constrains → a human approves → a simulated adapter acts → independent verification measures**

Every layer can refuse. The agent computes no business number and cannot approve or
execute. Policy fails closed across 13 gates. Verification owns its own thresholds and can
return `RECOVERY_NOT_VERIFIED`.

> Everything is a **synthetic incident with simulated execution**. No production
> infrastructure is contacted and **no real money is recovered**. Projected and measured
> figures are kept as separate quantities and never summed.

Complete and demo-ready — 611 tests passing, Alembic head `0007`, 250,000 observed transactions.

---

## Run it

Needs **Docker**, **Python 3.12+**, **Node 20+**.

```bash
# 1. database (port 5433, deliberately not 5432)
cd backend && docker compose up -d

# 2. environment + schema
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m alembic upgrade head

# 3. API -> localhost:8000
.venv/Scripts/python -m uvicorn aventum_api.app:app --port 8000

# 4. console -> localhost:5173   (separate terminal)
cd "../razorpay frontend new" && npm install && npm run dev
```

macOS/Linux: use `.venv/bin/python`. Defaults need no configuration —
`backend/.env.example` and `razorpay frontend new/.env.example` document every variable.

**The agent is optional** (`ollama serve && ollama pull qwen3:8b`). Analysis, simulation,
policy, approval, execution and verification all work without it, and the console says the
agent is unavailable rather than inventing an explanation.

---

## The demo

Open `localhost:5173` and walk: **Overview → the `gateway_C` incident → evidence & RCA →
simulations → recommendation & policy gates → request approval → approve → simulated
execution → independent verification → audit → batch impact.**

It produces `gateway_C → gateway_A @ 30%`: 9.26σ, 68.81% confidence, 74.03% evidence
strength, failure rate 20.83% → 17.42%, 79 transactions moved, **₹19,126.26 projected**
versus **₹14,668.00 actually recovered**, `RECOVERY_EFFECTIVE`, 7/7 integrity checks.

`POST /api/demo/reset` restores a clean start. It truncates an allow-list of workflow
tables and **cannot** reach observed transactions, the synthetic baseline, or the incident
analysis — no manual SQL, no fixture editing.

---

## CLI

Everything is driveable from the console, and also from the command line:

```bash
cd backend
.venv/Scripts/python -m aventum_ingest.cli    ingest | verify | status | datasets
.venv/Scripts/python -m aventum_synth.cli     generate | verify | cohorts | status
.venv/Scripts/python -m aventum_incident.cli  scenarios | status | handoff <run_id>
.venv/Scripts/python -m aventum_action.cli    decide <analysis_run_id>
.venv/Scripts/python -m aventum_action.cli    request <rec_id>
.venv/Scripts/python -m aventum_action.cli    approve <approval_id> --approver <name>
.venv/Scripts/python -m aventum_action.cli    execute <rec_id> <approval_id>
.venv/Scripts/python -m aventum_action.cli    audit <incident_id>
```

There is deliberately **no `--auto-approve`**. Approval is a separate human step.

`incident.cli scenarios` runs three cases: the flagship `gateway_C` degradation, a quiet
window that must stay silent, and an issuer-centred degradation that must **not** blame a
gateway — the check that the engine reasons from evidence rather than a memorised answer.

---

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest      # 611 tests
```

Covers ingestion and provenance, synthetic determinism, incident injection, detection,
evidence, RCA under adversarial ground-truth isolation, the decision core (every policy
gate failing closed independently, execution revalidation, real-thread concurrency), the
agent layer (typed tools, prompt-injection defence, budget enforcement), and the product
surface (verification's negative outcomes, batch-measurement honesty, and an API red-team
weighted toward refusals — forged state, injected numerics, duplicate
approval/execution/verification, policy bypass, credential exposure). Database tests use a
separate `aventum_test` database and never touch the canonical load.

---

## Architecture

| Package | Role |
|---|---|
| `aventum_ingest` | 250,000 canonical transactions; identity by SHA-256, never filename |
| `aventum_synth` | Explicitly modelled gateways, routing, latency, health |
| `aventum_incident` | Injection, detection, evidence, competing hypotheses, RCA |
| `aventum_counterfactual` | 13 bounded candidates, business impact, deterministic risk |
| `aventum_policy` | 13 fail-closed gates |
| `aventum_action` | Recommendation, human approval, simulated execution, append-only audit |
| `aventum_agent` | Qwen3 8B — interprets and orchestrates, computes nothing |
| `aventum_verification` | Independent post-action measurement, batch recovery |
| `aventum_api` | 19 endpoints, the browser's only surface |
| `razorpay frontend new/` | React + Vite + Tailwind operations console |

Four properties are **structural, not procedural**:

- `transactions` is never written after ingestion. Incidents *add* modelled failures in a
  separate layer; a `CHECK` constraint enforces it.
- The recommendation builder accepts **no numeric parameter** — every figure is read
  server-side from the persisted simulation, so a fabricated number has no way in.
- `actions.is_simulated` and `verifications.is_simulated` carry `CHECK (= true)`: the
  database refuses to record an execution as real.
- Incident ground truth lives in a table that no detection, RCA or agent code path reads.

---

## Rebuilding from source

Only if you want to regenerate rather than use a loaded database. The raw file under
`data/raw/` is read-only and never written to.

```bash
cd backend
.venv/Scripts/python -m aventum_ingest.cli ingest      # 250,000 rows, 21 checks
.venv/Scripts/python -m aventum_synth.cli  generate
.venv/Scripts/python -m aventum_incident.cli scenarios
```

Both fingerprints are reproducible and asserted by the test suite:

```
canonical   12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8
generation  e8414edd5a58c6cf04876e1bf48ca9a5564cf8d77da8eca4201c1732f52fe3c8
```

A file whose SHA-256 is not in `dataset_registry` is refused before anything is written,
so an unrelated file cannot be labelled as — or overwrite — the canonical dataset.
Re-running is safe: an identical source is skipped, and `--force` converges on the same
fingerprint rather than duplicating rows.

---

## Docs

**Contracts:** [canonical schema](docs/AVENTUM_CANONICAL_SCHEMA.md) ·
[database design](docs/DATABASE_DESIGN.md) · [data dictionary](docs/DATA_DICTIONARY.md) ·
[truth model](docs/DAY2B_TRUTH_MODEL.md) (what may and may not be claimed about synthetic data)

**Build reports:** [ingestion](docs/DAY2A_INGESTION_REPORT.md) ·
[synthetic baseline](docs/DAY2B_INFRASTRUCTURE_REPORT.md) ·
[incident intelligence](docs/DAY3_IMPLEMENTATION_REPORT.md) ·
[decision core](docs/DAY4A_IMPLEMENTATION_REPORT.md) ·
[agent](docs/DAY4B_IMPLEMENTATION_REPORT.md) ·
[product surface](docs/DAY5_IMPLEMENTATION_REPORT.md)

**Final quality gate:** [adversarial audit](docs/FINAL_PRE_SUBMISSION_AUDIT.md) against the
live system, and the [fix report](docs/FINAL_PRE_SUBMISSION_FIX_REPORT.md) — every defect
it found, reproduced, fixed and re-tested.

---

## Known limitations

No authentication — the approver identity is typed, not authenticated. No latency
telemetry and no capacity telemetry, both reported `UNAVAILABLE` rather than estimated.
Verification compares pre- and post-action on one cohort over a single window, not a
randomised control. The agent path has had less live exercise than the deterministic one.
The overview ranks by statistical significance, so a systemic incident can outrank the
flagship.
