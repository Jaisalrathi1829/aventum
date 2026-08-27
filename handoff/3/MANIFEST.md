# Day 3 Handoff — Incident Intelligence

Snapshot of everything created or modified by **Day 3: incident injection → simulated outcomes → anomaly detection → evidence → RCA**. Paths mirror the real repo (`aventum/backend/…`, `aventum/docs/…`).

All 24 files verified byte-identical to the live repo at copy time.

---

## Status

# DAY 3 COMPLETE

**362/362 tests pass** — 260 Day 2 unchanged (none removed or weakened) + 102 new.

`transactions` was never written to. Verified by content fingerprint, not row count.

---

## The three things to read first

**1. Approach B is a database constraint, not a convention.**

```sql
CHECK (NOT (observed_status = 'FAILED' AND simulated_status = 'SUCCESS'))
```

An incident *adds* modelled failures to the affected cohort. It never moves, rescues, or reallocates an observed one. A test issues a fully coherent rescue UPDATE and PostgreSQL rejects it — which is what makes the rejected Approach A unrepresentable rather than merely discouraged.

**2. Ground truth is structurally isolated, not flag-guarded.**

It lives in its own table, `incident_ground_truth`. No detection, evidence, hypothesis, or RCA module imports it or names it. Verified three ways: an AST scan of the diagnosis modules (docstrings stripped), RCA re-run with ground truth **deleted**, and RCA re-run with ground truth **corrupted to blame the wrong gateway**. All three produce a byte-identical `rca_fingerprint`.

This deviates from the Day 3 contract, which sketched a flagged column. A flag on a shared row leaks through an incautious `SELECT *`; a separate table does not.

**3. The confounding check is what makes the diagnosis honest.**

A degraded gateway drags every dimension that intersects it. In the first working run, `region=Delhi` (4.01σ) and `network=5G` (4.85σ) were flagged purely because they carried gateway_C traffic, and those shadows inflated rival hypotheses enough to drag the verdict to UNCERTAIN.

The fix asks whether a cohort's anomaly *survives removing the leading suspect on another dimension*. A real cause keeps its movement; a shadow collapses. This moved the golden scenario to **CONFIDENT** and demoted both spillovers with explicit contradicting evidence — with no hard-coded knowledge of which cohort was which.

---

## Scenario results (real 250,000-row dataset)

| | Scenario A — Golden | Scenario B — No incident | Scenario C — Alternative |
|---|---|---|---|
| Injected | gateway_C, 3 days | nothing | issuer SBI, all gateways |
| Affected cohort | 264 txns, 6.438% → **20.833%** | — | 552 txns, 4.871% → **21.920%** |
| Control group | 1,829 txns, **4.921%** | — | 1,541 txns, **4.932%** |
| Detection | **9.26σ** CRITICAL, rank #1 | **0 anomalies** | **17.82σ** CRITICAL, rank #1 |
| RCA verdict | **CONFIDENT 0.6396** | INSUFFICIENT_EVIDENCE | **CONFIDENT 0.7393** |
| RCA cause | gateway_C is degraded | *(declines to name one)* | Issuer SBI is failing |
| Blamed a gateway? | yes — correctly | no | **no** |
| Ground-truth match | ✅ | n/a | ✅ |

Scenario B is the control that makes the other two mean anything. Scenario C is the anti-overfit proof: same engine, same thresholds, different cause correctly named, with `gateway_degradation` ranked third carrying four contradicting evidence items.

---

## Files

### New package — `backend/aventum_incident/` (16 modules)

| Module | Role |
|---|---|
| `models.py` | 9 tables, all with machine-enforced provenance |
| `incident.py` | Definition, SHA-256 idempotency key, forward-only lifecycle |
| `simulate.py` | **The Approach B generator** |
| `rng.py` | Incident-keyed deterministic digest (reuses Day 2B primitives) |
| `metrics.py` | Set-based cohort aggregates + memoising `MetricStore` |
| `statistics.py` | Two-proportion z, effect weighting, severity bands |
| `detect.py` | Anomaly detection + alert discipline + suppression |
| `evidence.py` | 8 evidence types, all traceable to a named query |
| `hypothesis.py` | 5 competing categories, scored with supporting **and** contradicting evidence |
| `rca.py` | The conclusion — and the ability to decline |
| `evaluation.py` | The **only** module that reads ground truth, strictly post-hoc |
| `scenarios.py` | The three named scenarios |
| `pipeline.py` | Orchestration + persistence |
| `handoff.py` | The Day 4 interface |
| `cli.py`, `constants.py` | Entry point, thresholds and vocabulary |

### Migration

`0004_incident_intelligence.py` — 9 new tables. **No existing table altered.**

### Tests

`test_incident_intelligence.py` — 102 tests.

### Docs

`DAY3_IMPLEMENTATION_REPORT.md` (full results, methodology, limitations, Day 4 interface) and `DAY3_IMPLEMENTATION_CONTRACT.md` (amended with the four schema deviations).

### Modified

`migrations/env.py` (register Day 3 metadata), `tests/conftest.py` (truncate Day 3 tables), `README.md`.

---

## Two fixture bugs worth knowing about

Both would have produced **passing but meaningless** tests, and both had the same cause — modular strides in test fixtures are not independent of each other:

- `status = i % 20` alongside `day = 1 + (i % 12)` share a factor → every failure landed on days 1, 5, 9. The incident window contained no failures at all; the control rate was 0.0.
- `banks[i % 8]` alongside `day = 1 + (i % 12)` has `gcd(8,12) = 4` → each day saw only two of eight banks, and **SBI never occurred in the incident window**. The affected cohort was empty and the issuer scenario silently tested nothing.

Both replaced with independently salted hash-derived attributes.

---

## Reproducing

```bash
cd backend && .venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m aventum_incident.cli scenarios
```

Determinism verified: three consecutive runs produce identical fingerprints, including after a truncate and a reordering.

---

## Honesty boundary

An injected incident is a **synthetic construction**. It never occurred in history. A simulated outcome is a **modelled** outcome, never a measured one. No value in this layer is real Razorpay telemetry, routing, or incident data — and a demo must never imply otherwise.

GMV figures use authoritative observed `transactions.amount`; *which* transactions failed is modelled. Evidence records label this explicitly.

---

## Context not in this snapshot

Day 2A's canonical ingestion (`aventum/backend/aventum_ingest/`) and Day 2B's synthetic infrastructure (`aventum/backend/aventum_synth/`) are unchanged by Day 3 and are not copied here. See `handoff/2b/`, `handoff/2b-review/`, and `handoff/2-final/`.
