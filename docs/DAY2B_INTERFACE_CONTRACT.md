# Day 2B Interface Contract

Binding handoff specification produced by the Day 2A architecture review ([DAY2A_ARCHITECTURE_REVIEW.md](DAY2A_ARCHITECTURE_REVIEW.md)). Day 2B attaches **synthetic infrastructure** (gateway, routing, latency, response/error codes, gateway health) to the canonical transactions produced by Day 2A.

This document defines exactly what Day 2B may depend on, what it must preserve, and what it must never do. Everything below is verified against the live schema, not copied from the Day 2A report.

---

## 1. Required tables Day 2B may depend on

| Table | Status | Rows (verified) | Day 2B may |
|---|---|---|---|
| `transactions` | Implemented, migrated (`0001`) | 250,000 | **READ ONLY** — join to it, never write |
| `banks` | Implemented, seeded | 8 | READ ONLY |
| `ingestion_runs` | Implemented | 1 (`SUCCEEDED`) | READ ONLY — for lineage |
| `ingestion_rejects` | Implemented | 0 | READ ONLY |
| `transactions_staging` | Implemented | 0 at rest | **DO NOT TOUCH** — owned exclusively by the ingestion pipeline |
| `v_transactions_canonical` | Implemented (view) | — | READ ONLY — preferred read surface (exposes `issuer_bank_full_name`) |

Day 2B **must create its own tables** (`gateways`, `gateway_metrics`, `routing_policies`, …) per [DATABASE_DESIGN.md](DATABASE_DESIGN.md). It must not add synthetic columns to `transactions`.

## 2. Required keys and relationships

**Join key:** `transactions.transaction_id` — `text`, PRIMARY KEY, 250,000 distinct values, 100% unique (verified). Format `TXN0000000001`.

Day 2B synthetic tables must reference it as:

```sql
transaction_id text NOT NULL REFERENCES transactions(transaction_id)
```

**Allowed relationship shape:** one transaction → zero-or-more synthetic infrastructure rows. Day 2B must **not** assume 1:1 unless it enforces that itself with a UNIQUE constraint on its own table.

**Cardinality to design for:** 250,000 transactions today. If Day 2B writes one synthetic row per transaction, expect 250,000; per transaction-per-gateway-candidate, expect a multiple. See §9 on volume limits.

## 3. Fields Day 2B may rely on being present, non-null, and stable

Verified against live DDL. All are `NOT NULL` unless stated.

| Field | Type | Notes for Day 2B |
|---|---|---|
| `transaction_id` | text (PK) | Stable identity. Never regenerated for the same source row. |
| `timestamp` | timestamptz | Stored as an instant. Source was naive and **assumed IST (UTC+05:30)** — an assumption, not verified fact. Use `AT TIME ZONE 'Asia/Kolkata'` to recover source-local time. |
| `amount` | numeric(12,2) | INR. Always `> 0` (CHECK). |
| `status` | text | Exactly `SUCCESS` or `FAILED` (CHECK). No other state exists. |
| `payment_method` | text | `P2P` / `P2M` / `Bill Payment` / `Recharge` (CHECK). |
| `transaction_type` | text | **GENERATED from `payment_method`.** Read-only alias; identical value. Prefer `payment_method` in new code. |
| `merchant_category` | text, **NULLABLE** | `NULL` for every `P2P` row (44.98% of rows), non-null otherwise — enforced by CHECK in both directions. Day 2B must handle NULL. |
| `region` | text | 10 audited Indian states (CHECK). Sender-side only. |
| `device` | text | `Android` / `iOS` / `Web` (CHECK). |
| `network` | text | `4G` / `5G` / `WiFi` / `3G` (CHECK). |
| `sender_bank`, `receiver_bank` | text | FK → `banks.bank_code`. 8-bank universe. |
| `issuer_bank` | text | **GENERATED from `sender_bank`.** Read-only alias. |
| `fraud_flag` | boolean | **Retrospective label only.** See §6. |
| `source_dataset` | text | Provenance tag. Currently always `upi_transactions_2024`. See §6 caveat. |
| `ingestion_run_id` | bigint | FK → `ingestion_runs`. Row-level lineage. |

`issuer_bank_full_name` is **not** a column on `transactions` — it is served by `v_transactions_canonical` via `LEFT JOIN banks`. It is `NULL` for any bank without a confirmed NPCI alias (all 8 currently have one).

## 4. Required indexes (already present)

`pk_transactions` (transaction_id), `ix_transactions_timestamp`, `ix_transactions_sender_bank_timestamp`, `ix_transactions_status_timestamp`, `ix_transactions_region_timestamp`, `ix_transactions_ingestion_run`.

**Not indexed:** `payment_method`, `device`, `network`, `merchant_category`. If Day 2B's RCA queries filter heavily on these, add indexes **in a Day 2B migration** based on measured query plans — do not add speculatively.

## 5. Provenance requirements (binding)

Day 2A's canonical table contains **only observed and derived data**. Day 2B introduces the first synthetic data into the system. To preserve the Aventum integrity boundary:

1. **Synthetic data must live in its own tables**, never as added columns on `transactions`.
2. Every synthetic table must carry an explicit, machine-readable flag — `is_synthetic boolean NOT NULL DEFAULT true CHECK (is_synthetic = true)` per [DATABASE_DESIGN.md](DATABASE_DESIGN.md) — so no query can mistake it for observed fact.
3. Every synthetic row must record the **generation parameters and seed** used, so synthetic attachment is reproducible in the same way ingestion is.
4. Any view or agent tool that joins observed and synthetic data must expose the distinction in its output (column naming, a `provenance` field, or both). An RCA/LLM tool must never receive a flat record where `sender_bank` (observed) and `gateway_id` (invented) are indistinguishable.
5. Calibration values borrowed from the Nigerian routing dataset are **parameters, not data** ([ROUTING_DATASET_DECISION.md](ROUTING_DATASET_DECISION.md)). They must never be presented as observed evidence about a UPI transaction, and that dataset must never be row-joined to `transactions`.

## 6. Invariants Day 2B must preserve

- **Never write to `transactions`.** No INSERT, UPDATE, or DELETE. It is owned by the ingestion pipeline and is replaced wholesale on re-ingestion.
- **Never write to `transactions_staging`.**
- **Never overwrite** `transaction_id`, `timestamp`, `amount`, `status`, `payment_method`, `merchant_category`, `region`, `device`, `network`, `sender_bank`, `receiver_bank`, `fraud_flag`, `source_dataset`, `ingestion_run_id`.
- **`transaction_type` and `issuer_bank` cannot be written at all** — PostgreSQL rejects direct writes to generated columns.
- **`fraud_flag` must not be used as a pre-outcome / live-scoring signal.** Its assignment time relative to `status` is unstated in the source ([DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md)); it is retrospective only.
- **Incident ground truth must never enter the diagnosis path.** Per [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §12, `incident.*` fields exist only for offline evaluation.
- **Re-ingestion invalidates synthetic attachments.** Day 2A's promotion is `DELETE … WHERE source_dataset = 'upi_transactions_2024'` followed by re-INSERT. Because Day 2B's FKs point at `transaction_id`, a re-ingestion will either fail on the FK or orphan synthetic rows. **Day 2B must decide and document its rebuild policy** (cascade-and-regenerate, or version synthetic sets by `ingestion_run_id`). This is the single most important coupling to get right — see §8.

## 7. Expected query patterns Day 2B should be efficient for

```sql
-- Per-transaction synthetic attachment (primary write path)
SELECT transaction_id, timestamp, amount, status, payment_method,
       sender_bank, device, network, region
FROM transactions
ORDER BY transaction_id;          -- deterministic order for reproducible generation

-- Time-window incident/monitoring slice
SELECT ... FROM transactions
WHERE timestamp >= $1 AND timestamp < $2;                    -- uses ix_transactions_timestamp

-- Segment RCA slice
SELECT ... FROM transactions
WHERE sender_bank = $1 AND timestamp >= $2;                  -- uses ix_transactions_sender_bank_timestamp

-- Observed + synthetic evidence join
SELECT t.*, g.gateway_id, g.is_synthetic
FROM transactions t JOIN <day2b_table> g USING (transaction_id);
```

**Deterministic generation note:** if Day 2B assigns synthetic gateways pseudo-randomly, it must seed from a stable key (e.g. `hash(transaction_id, seed)`), not from row order or wall-clock time, so the same canonical dataset yields the same synthetic attachment — matching the determinism standard Day 2A already meets.

## 8. Versioning requirements

`ingestion_runs` gives Day 2A a run-versioned lineage. Day 2B should mirror it:

- Record a `generation_run_id` (or equivalent) per synthetic build, with seed, parameters, code version, and the `ingestion_run_id` it was generated against.
- Store the `ingestion_run_id` its synthetic rows correspond to, so a re-ingestion is detectable rather than silently producing a mismatched pairing.

Currently `transactions.ingestion_run_id` is `1` for all 250,000 rows (verified), so this pairing is trivially checkable today.

## 9. Known Day 2A limits Day 2B must design around

| Limit | Measured evidence | Implication |
|---|---|---|
| Ingestion fully materializes in memory | 268 MB peak Python heap for 250K rows (`tracemalloc`); extrapolates to ~10.7 GB at 10M rows | Fine at current scale. If Day 2B multiplies event volume, the ingestion path needs batching before that point. Stage boundaries already support it; no redesign needed. |
| Canonical fingerprint is table-wide, not per-source | `compute_canonical_fingerprint` aggregates all of `transactions` | If a second source dataset is ever loaded, the fingerprint and the 250,000-row verification both break. Scope them per `source_dataset` before multi-source. |
| Post-load verification asserts a hard-coded 250,000 | `constants.EXPECTED_ROW_COUNT` | Only asserted when the source SHA matches the audited file; safe today, but must be revisited before any second dataset. |
| No concurrency control | No advisory lock in `pipeline.py` | Two simultaneous ingestions serialize only incidentally on the staging `TRUNCATE` lock. Do not run ingestion concurrently. |

## 10. Schema changes required BEFORE Day 2B

**P1-1 is RESOLVED** — see [DAY2A_P1_FIX_REPORT.md](DAY2A_P1_FIX_REPORT.md). `source_dataset` is now resolved from the source file's SHA-256 via a `dataset_registry` table (migration `0002`), an unregistered file is refused before any canonical mutation, and promotion is scoped to the resolved identity so one dataset cannot displace another. Day 2B may treat `transactions.source_dataset` as trustworthy.

**No structural schema change to `transactions` is required for Day 2B.** The table, keys, constraints, and indexes are sufficient as they stand.

### Additional table Day 2B may read

| Table | Status | Day 2B may |
|---|---|---|
| `dataset_registry` | Implemented, migrated (`0002`), 1 seeded identity | **READ ONLY** — resolve a `source_dataset` back to its content hash and registration metadata |

### Additional invariants introduced by the P1-1 fix

- **`transaction_id` is a GLOBAL primary key**, not per-dataset. Two datasets can only coexist if their identifiers are disjoint. The canonical model is single-dataset, so this is not a constraint in practice — but Day 2B must not assume `(source_dataset, transaction_id)` is the key; `transaction_id` alone is.
- **Provenance is trustworthy but still table-wide in the fingerprint.** `compute_canonical_fingerprint` and the 250,000-row verification still cover the whole `transactions` table (P2-3, deferred). If Day 2B ever loads a second dataset, scope those first.
- **Re-ingestion of the canonical dataset requires the exact audited bytes.** Any edit to the source file changes its SHA and makes it unregistered, so canonical rows cannot be silently replaced by a modified file.

---

## Contract summary

Day 2B may **read** `transactions` / `v_transactions_canonical` / `banks` / `ingestion_runs`, must **write only its own new tables**, must key on `transaction_id`, must tag every synthetic row `is_synthetic` with reproducible generation parameters, must never mutate canonical rows or use `fraud_flag` as a live signal, and must explicitly define its rebuild policy for the case where `transactions` is re-ingested.
