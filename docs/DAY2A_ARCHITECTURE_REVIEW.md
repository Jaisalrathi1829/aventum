# Day 2A Architecture Review

Independent verification gate. Every conclusion below is backed by inspection of the current code, the live PostgreSQL schema, executed queries, or executed tests — not by the Day 2A report, which is treated throughout as a *claim* to be checked.

**No production code was modified during this review.** Empirical probes ran against a throwaway `aventum_probe` database (since dropped); the real canonical table was re-confirmed intact at 250,000 rows afterwards.

---

## Executive Verdict

# APPROVED WITH REQUIRED FIXES

> **STATUS UPDATE — P1-1 has been fixed.** See [DAY2A_P1_FIX_REPORT.md](DAY2A_P1_FIX_REPORT.md). `source_dataset` is now resolved from the source file's SHA-256 via a trusted `dataset_registry` (migration `0002`); unregistered files are refused before any canonical mutation; promotion is scoped to the resolved identity. Verified after the fix: raw SHA-256 and canonical fingerprint both unchanged, 250,000 rows, 0 rejected, and the full suite at **198 passed** (was 173; +25 provenance regression tests). P1-2 remains a binding Day 2B contract obligation, and all P2 items remain open and deferred as classified below.

Day 2A is substantially correct and considerably stronger than its report claims in some areas (the atomicity and idempotency guarantees are real and were re-proven here, not merely asserted). Both headline reproducibility claims verify exactly. The observed/derived/synthetic boundary is clean because the canonical table contains no synthetic data at all.

One **P1** defect must be fixed before Day 2B: `source_dataset` is a hard-coded constant rather than a property of the file actually ingested, so ingesting any other file via `--source` silently deletes the genuine canonical dataset and labels the replacement with a false provenance tag. This was reproduced empirically. Day 2B keys synthetic infrastructure to transactions whose provenance must be trustworthy, so this is a foundation-level correctness issue, not a cosmetic one.

Nothing found rises to P0. There is no data corruption in the current canonical load, no partial-state risk, and no observed/synthetic contamination.

---

## Schema Consistency

Compared four layers: [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) → [DATABASE_DESIGN.md](DATABASE_DESIGN.md) → live PostgreSQL DDL (`\d transactions`) → `models.py` → actual ETL output (`normalize.py` `CANONICAL_COLUMNS`).

| Field | Canonical | DB | ORM | ETL | Populated correctly? | Consistent? | Issue |
|---|---|---|---|---|---|---|---|
| `transaction_id` | ✓ | ✓ text PK | ✓ | ✓ | Yes — 250,000 distinct, 100% unique | ✓ | — |
| `timestamp` | ✓ | ✓ timestamptz NOT NULL | ✓ | ✓ | Yes — IST assumption applied, round-trips exactly | ✓ | — |
| `amount` | ✓ | ✓ numeric(12,2) CHECK >0 | ✓ | ✓ | Yes — 10.00–42,099.00, GMV ₹327,939,009.00 | ✓ | — |
| `status` | ✓ | ✓ CHECK IN (SUCCESS,FAILED) | ✓ | ✓ | Yes — 237,624/12,376 | ✓ | — |
| `payment_method` | ✓ | ✓ CHECK 4 values | ✓ | ✓ | Yes — matches Day 1 exactly | ✓ | — |
| `transaction_type` | ✓ | ✓ **GENERATED** from `payment_method` | ✓ `Computed` | n/a (DB-derived) | Yes — 0 mismatches | ✓ | Deviation A — see below |
| `merchant_category` | ✓ | ✓ nullable + P2P CHECK | ✓ | ✓ | Yes — NULL on all 112,445 P2P rows | ✓ | Vocabulary not CHECK-enforced (P2) |
| `region` | ✓ | ✓ CHECK 10 states | ✓ | ✓ | Yes — all 10 present | ✓ | — |
| `device` | ✓ | ✓ CHECK 3 values | ✓ | ✓ | Yes | ✓ | — |
| `network` | ✓ | ✓ CHECK 4 values | ✓ | ✓ | Yes | ✓ | — |
| `sender_bank` | ✓ | ✓ FK → banks | ✓ | ✓ | Yes — all 8 present | ✓ | — |
| `receiver_bank` | ✓ | ✓ FK → banks | ✓ | ✓ | Yes — all 8 present | ✓ | — |
| `issuer_bank` | ✓ | ✓ **GENERATED** from `sender_bank` | ✓ `Computed` | n/a (DB-derived) | Yes — 0 mismatches | ✓ | Deviation A — see below |
| `issuer_bank_full_name` | ✓ (class `derived`) | View only, not a column | Not modelled | Not written | Yes via view — `PNB → Punjab National Bank` verified | ✓ | Correct: avoids denormalizing |
| `fraud_flag` | Data dictionary only | ✓ boolean NOT NULL | ✓ | ✓ | Yes — 480 true | ✓ | Present in DB design + dictionary but absent from canonical-schema field tables (pre-existing Day 1 doc gap, P2) |
| `source_dataset` | ✓ | ✓ NOT NULL, nonempty CHECK | ✓ | ✓ | Value correct **for this run only** | ✗ | **P1 — hard-coded constant, not derived from the ingested file** |
| `ingestion_run_id` | Not in Day 1 docs | ✓ FK → ingestion_runs | ✓ | ✓ | Yes — all rows → run 1 | ✓ | Deviation B — see below |

All four layers agree on every field except `source_dataset`, whose *value semantics* diverge from its documented meaning.

---

## Schema Deviations

### Deviation A — `transaction_type` / `issuer_bank` as generated columns, `issuer_bank_full_name` via view

**Verdict: KEEP.**

- **Necessary?** Yes. Verified genuine contradiction: `AVENTUM_CANONICAL_SCHEMA.md` lines 23, 37–38 and `DATA_DICTIONARY.md` §`payment_method / transaction_type`, §`sender_bank / issuer_bank`, §`issuer_bank_full_name` all define these fields; `DATABASE_DESIGN.md`'s original column list omitted all three. Implementing only the physical design would have dropped fields the canonical schema promises to consumers.
- **Semantically correct?** Yes. The canonical schema's own wording is "same value as `payment_method`" and "copy of `sender_bank`". Generation implements exactly that.
- **Duplicated information?** Technically yes (stored twice), but **not divergent** information — that is the point. Live DDL confirms `generated always as (payment_method) stored`. Verified `SELECT COUNT(*) … WHERE transaction_type <> payment_method OR issuer_bank <> sender_bank` returns **0**, and `test_generated_alias_columns_cannot_be_written_directly` confirms PostgreSQL refuses direct writes. An ordinary duplicated column could drift; this cannot.
- **Semantic ambiguity?** Mild and acceptable. `payment_method` and `transaction_type` being identical could confuse a future reader or LLM tool into thinking they are distinct signals. Mitigated by the data dictionary documenting them jointly, and by the interface contract instructing Day 2B to prefer `payment_method`. Not worth restructuring.
- **Migration problems later?** No. Generated columns can be dropped or converted with a normal migration. Storage cost is ~2 text columns × 250K rows, immaterial at 86 MB total.
- **Queryable for RCA/agent tools?** Yes — ordinary columns from the query planner's perspective.
- **Cleaner design?** The alternative (omit them, expose via view only) would contradict the canonical schema's classification of `transaction_type`/`issuer_bank` as `observed` fields of the transaction. `issuer_bank_full_name` *is* handled that way correctly, because the canonical schema classes it `derived` and `banks.legal_name` already holds it. The split treatment is principled, not arbitrary.

### Deviation B — `ingestion_run_id` on `transactions`

**Verdict: KEEP.**

- **Necessary?** Yes. Day 2A's own auditability requirement is that any canonical row be traceable to the exact source that produced it. Without it, `transactions` and `ingestion_runs` are unlinked and the lineage chain breaks at the last hop.
- **Semantically correct?** Yes — NOT NULL FK, one run per row, verified all 250,000 rows point at run 1.
- **Consistent with canonical schema / dictionary?** It is an *addition* not present in either. It is infrastructure metadata rather than a payment-domain field, which is a reasonable category for the physical layer to own. It is now documented in `DATABASE_DESIGN.md`.
- **Complexity / ambiguity?** Minimal. One bigint + one index (`ix_transactions_ingestion_run`).
- **Migration problems?** One genuine coupling, documented in the interface contract §6: re-ingestion deletes and re-inserts rows, so Day 2B FKs pointing at `transaction_id` need an explicit rebuild policy. This is a Day 2B design obligation, not a Day 2A defect.

---

## Field Semantics

Checked against stored values and implementation, per the review's specific concerns:

- **`transaction_type` vs `payment_method`** — identical by construction, cannot diverge (verified 0 mismatches, direct writes rejected). Residual risk is *reader* confusion, not data confusion. Addressed by contract guidance. **OK.**
- **`issuer_bank`** — correctly represents the payer/remitter bank. `normalize.py` maps source `sender_bank` → canonical `sender_bank`, and the DB generates `issuer_bank` from it. Matches NPCI's "Remitter Bank" concept per [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md). **OK.**
- **`issuer_bank_full_name`** — correctly implemented as a `LEFT JOIN`, so a bank without a confirmed alias yields `NULL` rather than a fabricated name. All 8 current banks have confirmed aliases (`npci_reference_available = true`), but the *mechanism* does not assume that. Verified `PNB → Punjab National Bank`. **OK — does not imply universal mapping.**
- **`merchant_category`** — P2P rule correct and triple-enforced (normalization, validation, DB CHECK in both directions). Verified: 0 P2P rows with a category, 0 non-P2P rows without one, and NULL count (112,445) exactly equals the P2P count. **OK.**
- **`fraud_flag`** — stored as a plain retrospective boolean; nothing in Day 2A exposes it as a pre-outcome signal because there is no scoring or serving layer yet. Documented as post-hoc in [DATA_DICTIONARY.md](DATA_DICTIONARY.md). It *is* exposed in `v_transactions_canonical` without an inline warning — flagged as P2 and carried into the contract as a binding constraint on Day 2B. **OK for Day 2A.**
- **`timestamp`** — the IST assumption is applied via a fixed `+05:30` offset (correct: India has no DST), recorded verbatim in `ingestion_runs.timestamp_assumption`, and stored as `timestamptz` which honestly represents an instant rather than asserting a verified local time. Round-trip through `AT TIME ZONE 'Asia/Kolkata'` reproduces the Day 1 range exactly. **OK — assumption preserved without being presented as fact.**

---

## Observed / Derived / Synthetic Boundary

**Answer to the review's key question — "could a future RCA or Qwen tool accidentally treat synthetic data as observed historical fact?"**

**Not today.** `transactions` contains only observed fields plus two DB-generated aliases. There is no synthetic data anywhere in the Day 2A schema — no gateway, routing, latency, error-code, health, or incident table was created (verified: the only tables are `banks`, `transactions`, `transactions_staging`, `ingestion_runs`, `ingestion_rejects`, `alembic_version`). The Day 1.5 rule that the Nigerian routing dataset must never be joined is trivially upheld: it was never loaded.

**Classification status:** documented in the canonical schema and data dictionary; **not machine-readable in the database** — there is no `data_class` column or equivalent. For Day 2A this is harmless because the classification is uniform (everything is observed/derived). It becomes load-bearing the moment Day 2B introduces synthetic rows.

**Therefore:** not a Day 2A defect, but a **binding precondition on Day 2B**, specified in [DAY2B_INTERFACE_CONTRACT.md](DAY2B_INTERFACE_CONTRACT.md) §5 — synthetic data must live in separate tables carrying `is_synthetic … CHECK (is_synthetic = true)`, and any view or agent tool joining the two must surface the distinction. Recorded as **P1-2 (contract obligation)** rather than a code fix.

One minor point: `v_transactions_canonical` mixes an observed column set with the derived `issuer_bank_full_name` and gives the consumer no signal about which is which. **P2.**

---

## Data Lineage

Chain verified end to end:

```
raw file  →  SHA-256  →  ingestion_runs row  →  transactions.ingestion_run_id
```

| Link | Evidence |
|---|---|
| Raw file unchanged | `sha256sum` on disk = `8e46a45f…c89b6`, identical to the value recorded at ingest |
| Source identity captured | `ingestion_runs`: `source_file`, `source_filename`, `source_sha256` (CHECK length 64), `source_size_bytes`, `source_dataset` |
| Contract identity captured | `schema_version`, `code_version` — the determinism inputs |
| Timing captured | `started_at` NOT NULL, `finished_at`, `duration_seconds` |
| Drift evidence captured | `schema_drift_report` jsonb |
| Assumption captured | `timestamp_assumption` records that IST was assumed, not verified |
| Row-level provenance | `transactions.ingestion_run_id` NOT NULL FK; all 250,000 rows → run 1 |
| Verification evidence | `verification_report` jsonb + `canonical_fingerprint` |

**A future incident/evidence system can trace any transaction to its exact source file and ingestion run.** Confirmed by query: every row joins to a run carrying the source SHA.

**Caveat (the P1):** `source_dataset` is not part of that verified chain — it is a constant, so it can assert a provenance the file does not have. `ingestion_run_id` still recovers the truth, which is why this is P1 and not P0.

---

## Atomicity

Inspected `pipeline.py` rather than trusting the word "transaction". The promotion (lines 502–534) is a single `engine.begin()` block containing: `TRUNCATE staging` → `COPY` → `_verify_staging` → `DELETE FROM transactions WHERE source_dataset=…` → `INSERT … SELECT` → `TRUNCATE staging`.

| Failure point | Behaviour | Evidence |
|---|---|---|
| Before staging (missing/empty source) | Aborts before any DB write; **no run row created** | `test_missing_source_file_fails_before_any_db_write` asserts `ingestion_runs` count 0 |
| Schema drift | Aborts before the run is opened; canonical untouched | `test_existing_canonical_data_survives_a_drift_failure` — 30 rows before and after |
| During validation | Bad rows quarantined, good rows proceed | `test_invalid_rows_are_quarantined_not_discarded` |
| During staging load | Whole transaction rolls back | `test_staging_never_leaks_rows_into_a_failed_run` — staging 0 |
| During staging verification | Rolls back **before** the DELETE | `test_failure_during_staging_leaves_canonical_table_untouched` |
| **During promotion, after DELETE** | Rolls back; prior rows restored **byte-identically** | `test_failure_after_delete_rolls_back_the_whole_promotion` — asserts the *fingerprint* matches, not merely the row count |
| After audit metadata creation | Run marked `FAILED`, exception re-raised unmasked | `test_failed_run_is_recorded_with_its_error` |

The post-DELETE case is the one that actually matters and it is genuinely covered, including a fingerprint comparison rather than a row count. **No failure path can leave partial canonical rows or stale staging data.**

Two residual audit-metadata imperfections, both non-corrupting:

- A **FAILED run reports `rows_rejected = 0` while `ingestion_rejects` holds rows for that run** — reproduced: run 3 showed `rows_rejected=0` against 1 actual quarantine row. Counters are only written on the success path. **P2.**
- A process killed mid-run leaves an orphaned `RUNNING` row with no reaper. Harmless (the idempotency gate only matches `SUCCEEDED`), but it accumulates. **P2.**

---

## Idempotency

| Property | Result | Evidence |
|---|---|---|
| Same input → same canonical output | ✓ | Fingerprint `12dec963…f4b8` reproduced across runs including a full `downgrade base` → `upgrade head` rebuild |
| Same input rerun → no duplicates | ✓ | Second run returns `SKIPPED_IDEMPOTENT` in 0.05 s; table stays 250,000 |
| Forced rerun → converges, not duplicates | ✓ | `--force` → still 250,000 rows, identical fingerprint |
| Failed run + retry → safe | ✓ | Historically demonstrated live (run 1 FAILED on a serialization bug, run 2 SUCCEEDED cleanly); covered by `test_retry_after_a_failed_run_succeeds` |
| Clean rebuild → same result | ✓ | Verified by full drop/recreate cycle |
| **Current implementation still reproduces the documented fingerprint** | ✓ | Recomputed independently this session: `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8` — exact match, **no regression** |

Idempotency is keyed on `(source_sha256, schema_version, code_version)` and is explicit and auditable — each attempt, including skips, gets its own run row with an explanatory `notes` field. This satisfies the requirement that idempotency not be achieved merely by ignoring duplicate inserts.

---

## Schema Drift

`source_schema.py` reads the header with the raw `csv` module specifically so pandas cannot silently de-duplicate repeated column names — a genuinely thoughtful detail.

| Condition | Behaviour | Correct? |
|---|---|---|
| Missing **required** column | Hard failure before any canonical mutation | ✓ `test_missing_required_column_aborts_before_canonical_mutation` |
| Missing expected-but-unmapped column | Warning, recorded in drift report | ✓ appropriate |
| Unexpected new column | Warning, ingestion proceeds | ✓ cannot corrupt the documented mapping |
| Renamed column | Hard failure (surfaces as missing + unexpected) | ✓ deliberately does **not** guess the rename — `test_renamed_column_is_not_silently_adapted_to` |
| Duplicate column names | Hard failure | ✓ |
| Changed datatype (systemic) | Hard failure above a 50% sample threshold | ✓ |
| Isolated malformed rows | Quarantined with an inspectable reason | ✓ `test_malformed_timestamp_is_quarantined` |
| Invalid values | Quarantined | ✓ |

The 50% threshold is the right call and is documented in-code: it distinguishes *schema corruption* (column format genuinely changed → abort) from *isolated bad records* (quarantine). Protection against dangerous source changes is not weakened, because a real type change affects effectively all rows. **No weakness found here.**

---

## Database Constraints

Live DDL confirms 13 PK/FK/CHECK constraints plus NOT NULL on 14 columns.

**Answer to "if the ETL contained a bug, could invalid canonical data still enter the database?"** — Mostly no. Verified by bypassing the ETL entirely and inserting directly:

| Attempted violation | DB result |
|---|---|
| Duplicate `transaction_id` | rejected (PK) |
| Unknown `sender_bank` / `receiver_bank` | rejected (FK) |
| Unknown `ingestion_run_id` | rejected (FK) |
| `amount <= 0` | rejected (CHECK) |
| Invalid `status` / `payment_method` / `device` / `network` / `region` | rejected (CHECK) |
| P2P row **with** a merchant category | rejected (CHECK) |
| Non-P2P row **without** a merchant category | rejected (CHECK) |
| NULL in any of 10 required columns | rejected (NOT NULL) |
| Direct write to a generated column | rejected by PostgreSQL |
| **Arbitrary `merchant_category` (`'Cryptocurrency-Laundering'`)** | **ACCEPTED — gap** |

**One genuine gap: `merchant_category` has no vocabulary CHECK**, while every other categorical field does. An ETL bug could insert an arbitrary category string. Blast radius is a polluted RCA dimension, not a safety or integrity failure. **P2.**

Two apparent gaps I deliberately do **not** count as defects: `timestamp` outside the audited range and `amount` above the audited maximum are both accepted at DB level. That is **correct** — those are *audited-source* bounds, not universal invariants, and hard-coding them into the schema would reject legitimate future data (e.g. a 2025 live feed). They belong in ETL validation, which is exactly where they are.

---

## Performance

Measured rather than assumed.

| Aspect | Finding |
|---|---|
| Bulk insertion | ✓ PostgreSQL `COPY` into staging, then set-based `INSERT … SELECT`. No per-row INSERT. |
| N+1 queries | ✓ none — verification uses aggregate queries |
| Repeated full scans | Verification runs ~12 aggregate scans over 250K rows; acceptable and bounded |
| Indexes | 5 on `transactions`, matching the Day 1 segmentation dimensions |
| Fingerprint cost | 0.49 s for 250K rows (`string_agg` + sort) — cheap |
| Migration cost | Trivial (DDL only, 8-row seed) |
| **Memory** | **268 MB peak Python heap for 250K rows** (`tracemalloc`), because `_normalize_and_validate` materializes the full record list and `_copy_records_to_staging` then builds a complete `StringIO` buffer. Extrapolates to **~10.7 GB at 10M rows.** |
| Normalization cost | 16.7 s of the ~21 s total — the dominant cost, pure-Python per row |

**Answer to "does Day 2A create an architectural bottleneck forcing redesign at millions of events?"** — **No.** The stage boundaries (extract → normalize → validate → stage → promote) are exactly the right shape for batching; converting to chunked streaming is a localized change inside two functions, not an architectural redesign. But the *current implementation* will not survive 10M rows in one pass. Documented as a known limit in the interface contract; **P2**, no premature optimization warranted at 250K.

---

## Test Quality

**Assessment: ADEQUATE** (not STRONG).

**Strengths — these are real, not box-ticking:**
- Atomicity tests exercise the *hard* case (failure after DELETE) and assert a **fingerprint**, not just a row count.
- Idempotency tests cover skip, force-convergence, changed-source, and failed-run retry.
- DB constraint tests bypass the ETL entirely and write raw SQL — genuinely testing defense in depth rather than re-testing the ETL.
- The regression suite asserts Day 1 invariants including GMV and IST round-trip, against the real 250K file.
- `test_generated_alias_columns_cannot_be_written_directly` verifies the deviation's core guarantee.

**Weaknesses:**
- **No test covers the `source_dataset` provenance defect.** Worse, `test_changed_source_is_not_treated_as_already_ingested` ingests a *different* file and asserts only `status == SUCCEEDED` and `count == 12` — it **encodes the buggy behaviour as correct** without ever checking provenance. This is precisely a "test that passes while the real system is incorrect."
- No DB-level test for `merchant_category` vocabulary (consistent with the constraint gap — the missing constraint and the missing test share a blind spot).
- No test for the FAILED-run reject-counter inconsistency.
- No concurrency test.
- `test_p2p_rule_holds_after_load` and several others assert absence-of-violations, which would also pass on an empty table; they rely on sibling tests for row-count coverage.

173/173 passing was re-confirmed this session (28.0 s). The count is honest but, as the review anticipated, does not by itself establish quality — a real defect survived the suite.

---

## Security / Safety

Scoped to foundational risks in this pipeline only.

| Check | Finding |
|---|---|
| Committed secrets | None. The only credential is `aventum_local_dev` in `docker-compose.yml`, explicitly labelled a throwaway local-dev value. `.env` is gitignored. |
| DB credentials | Non-default user/db, container-scoped, bound to host port 5433 to avoid colliding with the user's real PostgreSQL 18 on 5432 — a deliberately safe choice. |
| Destructive reset commands | `TRUNCATE` is confined to `transactions_staging`, which the pipeline owns. Test fixtures truncate only within `aventum_test`. |
| Docker configuration | Reasonable: healthcheck, named volume, no privileged mode, no host mounts. Port is published on `0.0.0.0` — acceptable for local dev, worth noting if ever run on a shared network. **P2.** |
| Arbitrary SQL execution | None. All SQL is parameterized or static; no user input is interpolated into SQL. |
| Uncontrolled configuration | `AVENTUM_DATABASE_URL` / `AVENTUM_SOURCE_PATH` are the only knobs; both documented. |
| **CLI capable of destroying unrelated data** | **Yes, within its own table** — `ingest --source <any-file>` deletes all 250,000 canonical rows and replaces them. Scoped to `transactions` and recoverable by re-running the real ingestion, but it happens silently and with a false provenance label. This is the P1. |

---

## Day 2B Readiness

| Requirement | Status | Evidence |
|---|---|---|
| Stable transaction identity | ✓ | `transaction_id` text PK, 250,000 unique, deterministic across rebuilds |
| Stable FK target | ✓ | PK available for `REFERENCES transactions(transaction_id)` |
| Stable timestamp | ✓ | `timestamptz` NOT NULL, IST assumption documented and reversible |
| Queryable payment dimensions | ✓ with condition | bank/status/region/timestamp indexed; `payment_method`/`device`/`network` not — add in Day 2B based on measured plans |
| Provenance preservation | **Conditional** | Row-level lineage via `ingestion_run_id` is solid; `source_dataset` is unreliable (**P1**) |
| Observed/synthetic separation | ✓ for Day 2A, **contract-bound** for Day 2B | No synthetic data exists; separation rules specified in the interface contract |
| Reproducible synthetic attachment | ✓ enabled | Canonical dataset is deterministic and fingerprinted, so synthetic generation can be keyed to a stable input |
| Future versioning | ✓ pattern exists | `ingestion_runs` is a usable template for a `generation_run` analogue |
| Incident referenceability | ✓ | Transactions are addressable by id, time window, and segment |
| RCA queryability | ✓ with condition | All Day 1 RCA dimensions present; index tuning deferred |
| Efficient evidence retrieval | ✓ | 86 MB table, indexed time/segment access |

**Readiness: ready once the P1 is fixed.** Nothing about the table shape, keys, or constraints needs to change for Day 2B.

---

## Day 2B Interface Contract Summary

Produced as [DAY2B_INTERFACE_CONTRACT.md](DAY2B_INTERFACE_CONTRACT.md). Key bindings: Day 2B may read `transactions` / `v_transactions_canonical` / `banks` / `ingestion_runs` and must write **only its own new tables**; must key on `transaction_id`; must never mutate canonical rows or write generated columns; must tag every synthetic row `is_synthetic` with reproducible generation parameters and the `ingestion_run_id` it was generated against; must not use `fraud_flag` as a pre-outcome signal; must not row-join the Nigerian routing dataset; and must explicitly define its rebuild policy for re-ingestion, since promotion deletes and re-inserts canonical rows.

---

## Future Aventum Compatibility

| Component | Classification | Reasoning |
|---|---|---|
| **Monitoring** (success rate, failures, volume, GMV) | **READY** | All inputs present and indexed on timestamp; GMV verified exact |
| **RCA** (bank × method × device × network × synthetic infra) | **READY WITH CONDITION** | All observed dimensions present; add indexes for `payment_method`/`device`/`network` when query patterns are measured; synthetic infra is Day 2B's to add |
| **Counterfactual simulation** | **READY WITH CONDITION** | Transaction replay is fully supported (stable ids, preserved attributes, deterministic ordering). Routing dimension does not exist and must be synthetic — expected, per Day 1 §E |
| **Agent / tool-based evidence retrieval** | **READY WITH CONDITION** | Queryable and fast, but tools must expose observed/synthetic provenance once Day 2B lands — contract §5 |
| **Incident audit** (incident → evidence → recommendation → action → outcome) | **FUTURE SCHEMA CHANGE REQUIRED** | Those tables are designed but deliberately not created; expected, in scope for later phases |
| **Verification** (before/after comparison) | **READY WITH CONDITION** | Time-window comparison is efficient; post-action outcome still requires the synthetic continuation identified in Day 1 §G |

No component is blocked by a Day 2A design decision.

---

## Required Fixes

### P0 — Blockers

**None.**

### P1 — Must fix before Day 2B

**P1-1 — `source_dataset` is a hard-coded constant, not a property of the ingested file.**

*Evidence (reproduced on an isolated `aventum_probe` database):*
- `normalize.py:212` sets `"source_dataset": SOURCE_DATASET` — a module constant.
- `pipeline.py:509–510` promotes with `DELETE FROM transactions WHERE source_dataset = :ds`, same constant.
- Probe: ingested `datasetA.csv` (40 rows, ids `AAA…`) → 40 rows. Then ingested `totally_different.csv` (7 rows, ids `BBB…`, different bank) via the source override → **table dropped to 7 rows, all labelled `source_dataset = 'upi_transactions_2024'`, run status `SUCCEEDED`, verification "passed"** (because `dataset_invariants_asserted = False` for a non-audited SHA).

*Why it matters:* the canonical table asserts a provenance the data does not have, and the genuine dataset is silently destroyed. Day 2B attaches synthetic infrastructure keyed to these transactions; building on a provenance tag that can lie undermines the audit chain Aventum's incident/evidence layer depends on.

*Minimum recommended fix (do not implement in this review):*
1. Derive `source_dataset` from the ingested file (e.g. a config/CLI value, or a registry mapping SHA-256 → dataset name) instead of the constant, and record it on both `ingestion_runs` and `transactions`.
2. Refuse to ingest a file whose SHA-256 does not match the registered dataset unless an explicit flag (e.g. `--register-new-dataset <name>`) is supplied, so replacing the canonical dataset is always a deliberate act.
3. Add a regression test asserting that ingesting a different file either fails or is labelled with a different `source_dataset` — and fix `test_changed_source_is_not_treated_as_already_ingested`, which currently encodes the defect as expected behaviour.

**P1-2 — Observed/synthetic separation is documented but not yet machine-enforced (contract obligation, not a code fix).**

*Evidence:* no `data_class` column on `transactions`; classification lives only in prose. Harmless today (no synthetic data exists — verified table list contains no infrastructure tables), but load-bearing the moment Day 2B writes its first synthetic row.

*Minimum required action:* adopt [DAY2B_INTERFACE_CONTRACT.md](DAY2B_INTERFACE_CONTRACT.md) §5 as binding — synthetic data in separate tables with `is_synthetic … CHECK (is_synthetic = true)`, and any joined view or agent tool must surface the distinction. No Day 2A code change needed.

---

## Optional Improvements

- **P2-1** — Add a CHECK constraint on `merchant_category` vocabulary, for consistency with the other five categorical fields. *Evidence: arbitrary string accepted at DB level.*
- **P2-2** — Update `rows_read` / `rows_valid` / `rows_rejected` on the FAILED path. *Evidence: FAILED run reported `rows_rejected=0` against 1 real quarantine row.*
- **P2-3** — Scope `compute_canonical_fingerprint` and the row-count verification by `source_dataset` before any second dataset is ever loaded.
- **P2-4** — Take a PostgreSQL advisory lock for the duration of an ingestion run; concurrency is currently safe only incidentally, via the staging `TRUNCATE` lock.
- **P2-5** — Chunk `_normalize_and_validate` / `_copy_records_to_staging` before event volume approaches millions. *Evidence: 268 MB peak at 250K rows.*
- **P2-6** — Add a reaper or startup warning for orphaned `RUNNING` runs.
- **P2-7** — Add `fraud_flag` and observed/derived annotations to `v_transactions_canonical` (comment or column naming) so consumers see the caveat at the point of use.
- **P2-8** — `fraud_flag` appears in `DATABASE_DESIGN.md` and `DATA_DICTIONARY.md` but not in `AVENTUM_CANONICAL_SCHEMA.md`'s field tables; align the three (pre-existing Day 1 documentation gap, not introduced by Day 2A).
- **P2-9** — Bind the container port to `127.0.0.1:5433` rather than `0.0.0.0`.

---

## Final Decision Table

| Area | Status | Severity | Evidence | Required Action |
|---|---|---|---|---|
| Canonical schema | PASS | — | Four-layer comparison; all fields consistent | None |
| DB schema | PASS | — | Live `\d transactions`; matches migration exactly | None |
| Provenance | **FAIL** | **P1** | Probe: different file labelled `upi_transactions_2024`, genuine rows deleted | Derive `source_dataset` from the ingested file; guard replacement |
| ETL correctness | PASS | — | 250,000/250,000 valid, 0 rejected; all Day 1 distributions exact | None |
| Atomicity | PASS | — | Post-DELETE rollback verified by fingerprint, not row count | None |
| Idempotency | PASS | — | Fingerprint `12dec963…` reproduced incl. full rebuild | None |
| Schema drift | PASS | — | 8 drift conditions checked; corruption vs bad-record split is correct | None |
| Constraints | PARTIAL | P2 | 13 constraints enforce; `merchant_category` vocabulary does not | Add CHECK (deferrable) |
| Tests | ADEQUATE | P1 (coverage) | 173/173 pass, but a real defect survived; one test encodes the bug | Add provenance regression test |
| Performance | PASS | P2 | COPY + set-based promotion; 268 MB peak at 250K | Chunk before millions |
| Security | PARTIAL | P1 | No secrets/injection; CLI can silently replace canonical data | Covered by P1-1 fix |
| Day 2B interface | PASS | — | [DAY2B_INTERFACE_CONTRACT.md](DAY2B_INTERFACE_CONTRACT.md) produced | Adopt as binding |
| Day 2B readiness | CONDITIONAL | P1 | Table shape/keys/constraints sufficient; provenance must be fixed first | Fix P1-1, adopt P1-2 |

---

## Final Decision

# APPROVED WITH REQUIRED FIXES

Day 2A delivers a genuinely trustworthy canonical transaction table. Reproducibility, atomicity, idempotency, drift protection, and Day 1 fidelity were all independently re-verified rather than accepted on the report's word, and all held. The two documented schema deviations are both justified and are confirmed **KEEP**.

Before Day 2B begins: fix **P1-1** (provenance labelling) and adopt **P1-2** (the observed/synthetic separation rules in the interface contract). No structural change to `transactions` is required. All P2 items may be deferred without risk.
