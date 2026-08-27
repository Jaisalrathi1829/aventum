# Day 2A Ingestion Report — Canonical Transaction Pipeline

Result of implementing and executing the production-grade canonical transaction ingestion pipeline. Every figure below is measured from an actual run, not estimated. Reproduce with the commands in [../README.md](../README.md).

> **Amended after the P1-1 provenance fix** ([DAY2A_P1_FIX_REPORT.md](DAY2A_P1_FIX_REPORT.md)). `source_dataset` is no longer a hard-coded constant: it is resolved from the source file's SHA-256 through a `dataset_registry` (migration `0002`), and an unregistered file is refused before any canonical mutation. The measured results below are unchanged — same SHA-256, same 250,000 rows, same canonical fingerprint — because the fix changed how provenance is *established*, not the canonical content. Test count is now **198** (was 173) and post-load checks are now **21** (was 20, after the provenance checks were re-scoped by dataset/run).

---

## Source

| Property | Value |
|---|---|
| Source path | `data/raw/UPI Transactions 2024 Dataset/upi_transactions_2024.csv` |
| Filename | `upi_transactions_2024.csv` |
| **SHA-256** | `8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6` |
| Size | 29,811,789 bytes |
| Source row count | 250,000 (data rows, header excluded) |
| Source column count | 17 |
| Schema version (ingestion contract) | `1.0.0` |
| Code version | `0.1.0` |
| Encoding / delimiter | UTF-8, comma |

The raw file was opened read-only throughout. Its SHA-256 was re-verified on disk after the run and is unchanged, confirming `data/raw/` was never mutated.

### Schema drift check (runs before any canonical mutation)

**Result: no drift.** All 17 expected columns present, no duplicates, no unexpected columns, and both critical-typed columns (`amount (INR)`, `timestamp`) still conform. Had a required column been missing, renamed, duplicated, or type-changed, the run would have aborted here with the canonical table untouched.

---

## Transformation

### Field mapping applied

| Source column | Canonical field | Rule |
|---|---|---|
| `transaction id` | `transaction_id` | rename (strip header space), trim whitespace |
| `timestamp` | `timestamp` | parse with explicit `%Y-%m-%d %H:%M:%S`, attach IST |
| `amount (INR)` | `amount` | rename, `Decimal` at scale 2 (never via float) |
| `transaction_status` | `status` | rename, upper-case canonicalization |
| `transaction type` | `payment_method` | rename |
| `transaction type` | `transaction_type` | **generated column** — see Deviations |
| `merchant_category` | `merchant_category` | rename + P2P NULL rule (below) |
| `sender_state` | `region` | rename |
| `device_type` | `device` | rename |
| `network_type` | `network` | rename |
| `sender_bank` | `sender_bank` | rename |
| `receiver_bank` | `receiver_bank` | rename |
| `sender_bank` | `issuer_bank` | **generated column** — see Deviations |
| `fraud_flag` | `fraud_flag` | 0/1 → boolean |
| — | `source_dataset` | constant `upi_transactions_2024` (provenance) |
| — | `ingestion_run_id` | FK to the run that produced the row (provenance) |

**Deliberately unmapped source columns:** `sender_age_group`, `receiver_age_group`, `hour_of_day`, `day_of_week`, `is_weekend`. The last three are exact deterministic derivatives of `timestamp` ([DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md) §1) carrying zero independent information; the two age-group columns have no canonical field defined in [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md). Their absence is reported, not silently ignored.

### P2P cleaning rule

`payment_method = 'P2P'` → `merchant_category = NULL`. Applied to **112,445 rows** (44.98% of the dataset). No merchant information is invented for any row; for non-P2P rows the source category is preserved verbatim. The rule is enforced in **both** directions at three independent layers: normalization, validation, and a PostgreSQL CHECK constraint.

### Timestamp assumption

Source timestamps carry no timezone designator. Per [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md), they are assumed to be **IST (Asia/Kolkata, UTC+05:30)** and stored as `timestamptz`. A fixed +05:30 offset is used (India observes no DST, so this is exact for all 2024 dates and avoids depending on the host's tz database). The assumption is recorded verbatim in `ingestion_runs.timestamp_assumption` for every run and is **not represented as independently verified fact**. Timestamps are parsed with an explicit format string so no locale- or heuristic-dependent reinterpretation (e.g. DD/MM vs MM/DD) can occur.

### Determinism

No randomness, no non-deterministic ordering (rows keep source order via `source_row_index`), no wall-clock time as a transformation input (used only for audit metadata), no locale-dependent parsing. Dependency versions are pinned in `backend/requirements.txt`. **Verified:** three independent runs — including one from a completely dropped-and-recreated schema — produced the identical canonical fingerprint.

---

## Validation

| Metric | Value |
|---|---|
| Rows read | 250,000 |
| Rows valid | 250,000 |
| **Rows rejected** | **0** |
| Quarantine table rows | 0 |

**Validation failures by category: none.** All checks passed for every row: identity present/unique, timestamp valid and within the Day 1 audited range, amount non-null/numeric/`> 0` and within audited bounds, status in `{SUCCESS, FAILED}`, payment-method/device/network/region vocabularies, both banks within the audited 8-bank universe, the P2P merchant rule in both directions, and provenance populated.

This matches the Day 1 expectation exactly — the audit found this source fully clean, so any rejection would have signalled a new, previously undocumented issue.

---

## Load

| Metric | Value |
|---|---|
| Rows inserted | 250,000 |
| Staging → promotion | staged 250,000 → staging verified → atomically promoted |
| Duration (full pipeline) | **20.91 s** (clean-state run); 21.4–21.6 s across repeat runs |
| Final `transactions` table size | 86 MB (including indexes) |
| Bulk method | PostgreSQL `COPY` into staging, then set-based `INSERT ... SELECT` |

**Atomicity:** the canonical table is touched only inside one transaction that deletes this source's prior rows and inserts the new set, then truncates staging. Any failure at any point rolls the whole thing back. Both failure windows are covered by tests: failure *before* the delete, and failure *after* the delete but during the insert — the latter verified to restore the pre-run state byte-for-byte via fingerprint comparison, not merely by row count.

**Idempotency:** re-running the identical source records a `SKIPPED_IDEMPOTENT` run and changes nothing (measured: 0.05 s, table still 250,000 rows). `--force` re-executes and converges on the identical fingerprint rather than duplicating. A `FAILED` prior run never blocks a retry.

---

## Verification

All 20 post-load checks passed, re-queried independently from the database rather than reusing the pipeline's own counters. (Running `cli verify` standalone reports 19 of these — `rows_not_attributed_to_this_run` only applies in the context of a specific ingestion run.)

| Check | Expected | Actual |
|---|---|---|
| Total rows | 250,000 | 250,000 |
| Unique transaction IDs | 250,000 | 250,000 |
| Duplicate rows | 0 | 0 |
| Status distribution | SUCCESS 237,624 / FAILED 12,376 | matched exactly |
| Payment-method distribution | P2P 112,445 / P2M 87,660 / Bill Payment 37,368 / Recharge 12,527 | matched exactly |
| Device distribution | Android 187,777 / iOS 49,613 / Web 12,610 | matched exactly |
| Network distribution | 4G 149,813 / 5G 62,582 / WiFi 25,134 / 3G 12,471 | matched exactly |
| Amount range | 10 – 42,099 | 10.00 – 42,099.00 |
| Total GMV | ₹327,939,009 | ₹327,939,009.00 |
| All 8 expected banks present | yes | yes (sender and receiver) |
| All 10 expected states present | yes | yes |
| P2P rows with a merchant category | 0 | 0 |
| Non-P2P rows missing a merchant category | 0 | 0 |
| Generated alias mismatches | 0 | 0 |
| `fraud_flag` true count | 480 | 480 |
| Rows with bad provenance | 0 | 0 |
| Rows not attributed to this run | 0 | 0 |
| Timestamp range (round-tripped through IST) | 2024-01-01 00:05:10 → 2024-12-30 23:55:40 | matched exactly |
| Database constraints active | ≥ 13 PK/FK/CHECK on `transactions` | 13 |
| Staging rows after promotion | 0 | 0 |

**Canonical dataset fingerprint:** `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8`

A SHA-256 over every canonical row rendered in a fixed column order and sorted by `transaction_id`, so it is independent of physical row order. Identical across all successful runs, including from a clean schema — this is the value future runs should reproduce from the same raw source.

**Note on scope of these expectations:** the distribution checks describe the specific audited file. The pipeline asserts them only when the ingested bytes hash to `AUDITED_SOURCE_SHA256`; for any other source they are recorded as explicitly *skipped* (never silently "passed") while all structural invariants still run.

---

## Database objects created

Alembic revision `0001` (`backend/migrations/versions/0001_canonical_ingestion_core.py`), applied from a clean state:

- `banks` — dimension, seeded with the 8 audited banks and their confirmed NPCI legal names
- `transactions` — canonical fact table, 13 PK/FK/CHECK constraints + 5 indexes
- `transactions_staging` — staging half of the atomic load, same CHECK constraints
- `ingestion_runs` — audit metadata
- `ingestion_rejects` — quarantine
- `v_transactions_canonical` — view exposing `issuer_bank_full_name` via join

Later-phase tables (`gateways`, `gateway_metrics`, `routing_policies`, `incidents`, `incident_evidence`, `simulations`, `simulation_results`, `recommendations`, `actions`, `verification_results`, `audit_events`) were deliberately **not** created.

---

## Tests

| Suite | Tests | Result |
|---|---|---|
| `test_normalize.py` — field mapping, normalization, timestamp, P2P rule, status, bank | 41 | passed |
| `test_validate.py` — duplicate IDs, invalid amount/status/timestamp/device/network, missing fields | 53 | passed |
| `test_db_constraints.py` — PK, FK, CHECK, NOT NULL, P2P constraint, generated columns | 35 | passed |
| `test_pipeline.py` — success, quarantine, drift failure, idempotency, failed-run recovery, atomicity | 26 | passed |
| `test_regression_full_source.py` — complete 250K ingestion vs. Day 1 invariants | 18 | passed |
| **Total** | **173** | **173 passed, 0 failed** |

Runtime: 26.4 s for the full suite. Database tests run against a separate `aventum_test` database created and migrated per session, so they never touch the real canonical load.

---

## Deviations

Two deviations from the Day 1 documents were found and resolved. Neither is a silent reinterpretation; both are recorded in [DATABASE_DESIGN.md](DATABASE_DESIGN.md) as amendments.

### 1. Canonical schema vs. database design — missing alias fields (contradiction)

**The contradiction.** [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) and [DATA_DICTIONARY.md](DATA_DICTIONARY.md) both define `transaction_type`, `issuer_bank`, and `issuer_bank_full_name` as canonical fields. [DATABASE_DESIGN.md](DATABASE_DESIGN.md)'s `transactions` column list omitted all three. The Day 2A brief explicitly requires the `transaction type → transaction_type` and `sender_bank → issuer_bank` mappings.

**Why it matters.** Implementing only the physical design would silently drop two fields the canonical schema promises to consumers. Implementing them as ordinary duplicated columns would create two independently-mutable copies of the same fact, free to drift apart over time.

**Minimum justified change.** `transaction_type` and `issuer_bank` are implemented as PostgreSQL `GENERATED ALWAYS AS (...) STORED` columns over `payment_method` and `sender_bank`. The canonical schema's own wording is "same value as `payment_method`" and "copy of `sender_bank`", so generation satisfies the specification exactly while making divergence *structurally impossible* rather than merely validated — which is what the defense-in-depth requirement asks for. PostgreSQL rejects any attempt to write them directly (covered by test).

`issuer_bank_full_name` was **not** added as a stored column: `banks.legal_name` already holds that value, the canonical schema classes the field `derived`, and denormalizing it across 250,000 rows would create a second source of truth. It is served by the `v_transactions_canonical` view instead.

### 2. `ingestion_run_id` added to `transactions`

Not present in the original design, but required by the Day 2A auditability criterion that canonical records be traceable to the run that produced them. Added as a NOT NULL FK to `ingestion_runs`.

### Deviations from Day 1 audited data expectations

**No deviations from Day 1 audited expectations.** Every measured distribution, count, range, and vocabulary matched the Day 1 audit exactly: 250,000 rows, 0 duplicates, 0 rejects, identical status/payment-method/device/network distributions, identical amount range and GMV, all 8 banks and 10 states present, and the timestamp range round-tripping precisely through the documented IST assumption.

### Implementation notes worth recording

Two design corrections were made during implementation, both surfaced by tests rather than by inspection:

- **Verification scope.** The first implementation asserted the Day 1 distribution expectations against *every* load, which made verification meaningless for any other source. Split into structural invariants (always asserted) and dataset invariants (asserted only when the source SHA-256 matches the audited file, explicitly recorded as skipped otherwise).
- **Type-drift threshold.** The first schema-drift check failed the entire ingestion if *any* sampled value in a critical column failed to parse. That contradicts the quarantine requirement — one malformed row in 250,000 should be quarantined with an inspectable reason, not abort the run. A column now counts as type-changed only when more than 50% of sampled values fail, which is the point at which the documented mapping genuinely can no longer be executed.
