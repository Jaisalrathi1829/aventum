# Day 2A P1-1 Fix Report — Source Dataset Provenance

Fix for the single P1 issue raised by [DAY2A_ARCHITECTURE_REVIEW.md](DAY2A_ARCHITECTURE_REVIEW.md). Scope was limited to P1-1; no P2 items were addressed and Day 2B was not started.

---

## Defect

`transactions.source_dataset` and `ingestion_runs.source_dataset` were populated from a module constant rather than from the file actually being ingested. Consequences, reproduced by the review:

1. Any file routed through `--source` was labelled `source_dataset = 'upi_transactions_2024'`.
2. Promotion deleted rows scoped to that same constant, so an unrelated file **silently destroyed the genuine canonical dataset** and took its name.
3. The run was recorded `SUCCEEDED` and verification "passed", because Day 1 dataset invariants are skipped for an unrecognised SHA-256.

## Root Cause

Provenance was treated as a **property of the pipeline** (a compile-time constant) rather than a **property of the data** (established from content). There was no mechanism binding a dataset name to the bytes it describes, so nothing could distinguish "the audited canonical file" from "some other CSV with a compatible header".

Concretely, before the fix:

- `normalize.py` wrote `"source_dataset": SOURCE_DATASET` for every record.
- `pipeline.py` opened each run with `"dataset": SOURCE_DATASET` and promoted with `DELETE FROM transactions WHERE source_dataset = SOURCE_DATASET`.
- `transactions.source_dataset` additionally carried a `DEFAULT 'upi_transactions_2024'`, so even a bare INSERT acquired the canonical name for free.

## Trusted Dataset Identity Model

Identity is now resolved from content, never from filename:

```text
source file  ->  SHA-256  ->  registered dataset identity  ->  source_dataset
```

A new `dataset_registry` table (migration `0002`) binds:

| Column | Role |
|---|---|
| `source_sha256` | **PRIMARY KEY** — identity is keyed on content |
| `dataset_name` | **UNIQUE** — one name can never be rebound to different bytes |
| `schema_version` | the ingestion contract the binding was made under |
| `source_filename`, `source_size_bytes` | **metadata only**, never consulted during resolution |
| `registered_at`, `registered_by`, `notes` | audit trail |

The trust boundary this establishes:

- **Renaming a file does not change its identity** — same bytes, same SHA, same dataset name.
- **Editing a file destroys its identity** — new bytes, new SHA, unregistered.

Both directions are tested. The canonical dataset is seeded by the migration, so its identity is available from a clean migrate with no manual step:

```text
upi_transactions_2024  <->  8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6
```

Seeding a verified hash→name pair is not "hard-coding provenance": the name is only ever assigned to a file that actually hashes to that value.

## Registration Behavior

Registration and canonical replacement are separate operations, exposed as separate commands.

```bash
python -m aventum_ingest.cli register --source <path> --name <dataset_name>
```

Registration:

- binds name ↔ SHA-256 ↔ schema version, with `registered_by` / `notes` for audit;
- **loads nothing and replaces nothing** — verified: the canonical fingerprint is byte-identical before and after;
- refuses to rebind a known name to different content;
- refuses to rebind known content to a different name;
- is idempotent when re-registering an identical pair;
- cannot hijack `upi_transactions_2024`, because that name is already bound.

`python -m aventum_ingest.cli datasets` lists registered identities.

## Unknown Source Behavior

An unregistered SHA-256 is refused **before** the run row is opened and long before any DELETE:

```text
UNKNOWN DATASET -- ingestion refused (canonical data unchanged)
  file   : .../rogue.csv
  sha256 : c610c0135bfd5f24fe59df1ae25e79bd47006c07b080820a0a6914ee2e32c538
  size   : 473 bytes
Dataset identity is established by content hash, never by filename, so this file
cannot be assigned an existing dataset's name.
```

CLI exit code **5**. Measured against the live canonical dataset:

| | Before attempt | After attempt |
|---|---|---|
| Canonical fingerprint | `12dec963…f4b8` | `12dec963…f4b8` (identical) |
| Rows | 250,000 | 250,000 |
| Distinct `source_dataset` | `upi_transactions_2024` | `upi_transactions_2024` |
| Ingestion runs | 2 | 2 (none opened) |

No ingestion-run row is written for an unidentifiable file. A run row must carry a `source_dataset`, and there is no honest value for unrecognised bytes — inventing a sentinel would be a smaller version of the defect being fixed. The attempt surfaces as a raised error and a non-zero exit code instead.

## Canonical Replacement Authorization

Replacement is now explicit in two independent ways:

1. **Identity must be registered.** An unknown file cannot reach promotion at all.
2. **Promotion is scoped to the resolved identity.** `DELETE FROM transactions WHERE source_dataset = <resolved name>` — so ingesting dataset B can only ever remove B's own rows. One dataset can no longer displace another (tested).

Replacing `upi_transactions_2024` therefore requires a file that genuinely hashes to the registered canonical value, i.e. the audited file itself.

All Day 2A atomicity guarantees are unchanged: identity resolution is a read-only lookup inserted before the existing integrity → drift → stage → verify → promote chain. Rollback behaviour, quarantine, and idempotency are untouched and still covered by their original tests.

## Tests Added / Modified

**Added — `tests/test_dataset_provenance.py` (25 tests)** covering the required matrix:

| Case | Coverage |
|---|---|
| A — Unknown source | rejected; fingerprint identical before/after; no false `source_dataset`; no run opened; no `SUCCEEDED` run |
| B — Registration | binds name/SHA/schema; does not mutate canonical; persisted and auditable; cannot rebind either side; idempotent; canonical name cannot be hijacked |
| C — Registered ingestion | succeeds; `ingestion_runs.source_dataset` correct; every row carries it; run and row provenance agree; lineage reaches the source hash |
| D — Real dataset | resolves to `upi_transactions_2024`; 250,000 rows; 0 rejected; fingerprint reproduced |
| E — Old-bug regression | a different file is never labelled canonical; a *registered* non-canonical file never acquires the canonical name; one dataset cannot delete another's rows |
| Trust boundary | renaming preserves identity; editing destroys it; registry is hash-keyed |
| Schema binding | a dataset registered under a different contract version is refused |

**Modified:**

- `tests/test_pipeline.py::test_changed_source_is_not_treated_as_already_ingested` — **rewritten.** It previously asserted only `status == SUCCEEDED` and `count == 12` ("replaced, not appended"), which encoded the defect as expected behaviour. It now asserts each file keeps its **own** registered identity and that neither displaces the other.
- `tests/conftest.py` — added `TEST_DATASET` / `CANONICAL_DATASET_NAME`, a `register_source` fixture, and a `registered_source` fixture; the `engine` fixture now clears per-test registrations while preserving the migration-seeded canonical identity.
- `tests/test_pipeline.py` — ingestion tests use `registered_source` (an operator must establish identity first); tests asserting pre-identity guards (missing file, empty file, schema drift) deliberately keep an **unregistered** source.
- `tests/test_normalize.py`, `tests/test_validate.py` — updated for the `normalize_row(..., source_dataset)` signature. `test_normalize_row_maps_every_documented_source_column` now asserts the record carries `TEST_DATASET`, so it fails if a hard-coded canonical constant is reintroduced.

## Full Test Results

```text
198 passed in 50.61s
```

| Suite | Tests |
|---|---|
| `test_normalize.py` | 41 |
| `test_validate.py` | 53 |
| `test_db_constraints.py` | 35 |
| `test_pipeline.py` | 26 |
| `test_regression_full_source.py` | 18 |
| `test_dataset_provenance.py` (new) | 25 |
| **Total** | **198** (was 173; +25 new, 0 removed, 0 weakened) |

No test was deleted or relaxed to obtain a green run.

## Real Dataset Verification

Clean-state run — full migration chain dropped to `base` and rebuilt through `0001 → 0002`, then ingested:

```text
Run id          : 1
Status          : SUCCEEDED
Dataset identity: upi_transactions_2024  (resolved from content hash)
Source SHA-256  : 8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6
Rows read       : 250,000
Rows valid      : 250,000
Rows rejected   : 0
Rows inserted   : 250,000
Duration        : 19.87s
Fingerprint     : 12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8
Verification    : All 21 post-load verification checks passed.
```

Final state: 250,000 rows, `{'upi_transactions_2024': 250000}`, 1 registered identity, 1 `SUCCEEDED` run, run/row provenance **CONSISTENT**, and `transactions.source_dataset` column default now `(none - removed)`.

## SHA-256 Verification

| | Value | Status |
|---|---|---|
| Expected | `8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6` | — |
| Recomputed from disk | `8e46a45fd12c3e9e75a7cf1ac73604bdd9b2bd72859e3374d0153256ac4c89b6` | **MATCH** |

The raw file was never modified.

## Canonical Fingerprint Verification

| | Value | Status |
|---|---|---|
| Expected (pre-fix) | `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8` | — |
| After fix, clean-state ingest | `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8` | **UNCHANGED** |

As expected: the fix changes how provenance is *established*, not the canonical content. `source_dataset` participates in the fingerprint, and it still resolves to `upi_transactions_2024` — now because the file's hash proves it, rather than because a constant asserted it.

## Files Changed

**New**

| File | Purpose |
|---|---|
| `backend/aventum_ingest/dataset_registry.py` | Identity resolution and registration |
| `backend/migrations/versions/0002_dataset_registry.py` | `dataset_registry` table, canonical seed, drops the `source_dataset` default |
| `backend/tests/test_dataset_provenance.py` | Provenance regression matrix (25 tests) |
| `docs/DAY2A_P1_FIX_REPORT.md` | This report |

**Modified**

| File | Change |
|---|---|
| `backend/aventum_ingest/constants.py` | `SOURCE_DATASET` → `CANONICAL_DATASET_NAME`, documented as *not* the ingestion provenance value |
| `backend/aventum_ingest/normalize.py` | `normalize_row` takes a required `source_dataset` argument |
| `backend/aventum_ingest/pipeline.py` | Resolves identity before any mutation; threads it into the run row, records, promotion DELETE, and verification |
| `backend/aventum_ingest/models.py` | Added `DatasetRegistry` model; removed the `source_dataset` server default |
| `backend/aventum_ingest/verify.py` | Provenance checks scoped by dataset/run instead of table-wide |
| `backend/aventum_ingest/cli.py` | New `register` and `datasets` commands; unknown-dataset exit code 5; prints resolved identity |
| `backend/tests/conftest.py` | Registration fixtures; registry isolation between tests |
| `backend/tests/test_pipeline.py` | Rewrote the defective test; ingestion tests register first |
| `backend/tests/test_normalize.py`, `test_validate.py` | Updated for the new signature |
| `backend/pytest.ini` | Registered the `slow` marker |
| `docs/DATABASE_DESIGN.md`, `docs/DAY2A_INGESTION_REPORT.md`, `docs/DAY2A_ARCHITECTURE_REVIEW.md`, `docs/DAY2B_INTERFACE_CONTRACT.md`, `README.md` | Updated for the registry and the resolved P1 |

### Two consequential decisions worth flagging

**1. Verification provenance checks were re-scoped.** `rows_with_bad_provenance` and `rows_not_attributed_to_this_run` compared the *whole table* against a single dataset name — the same "one global dataset" assumption that produced P1-1. Once datasets can legitimately differ, those checks flagged another dataset's correct provenance as an error. They are now three scoped checks, none weaker than before:

- `rows_missing_lineage` — any row with a null run or dataset;
- `rows_written_by_this_run_with_wrong_dataset` — a row this run wrote under the wrong name;
- `rows_of_this_dataset_not_attributed_to_this_run` — a row of this dataset that promotion should have replaced.

**2. `transaction_id` is a global primary key.** Surfaced while testing dataset coexistence: two datasets can only coexist if their identifiers are disjoint, otherwise the PK rejects the second. This is pre-existing behaviour, not introduced here, and is correct for the single-dataset canonical model. It is noted in [DAY2B_INTERFACE_CONTRACT.md](DAY2B_INTERFACE_CONTRACT.md) rather than changed, since the review confirmed no schema change to `transactions` is required for Day 2B.

## Confirmation That Day 2B Was Not Started

No synthetic infrastructure was built. Verified: the database contains only `alembic_version`, `banks`, `dataset_registry`, `ingestion_rejects`, `ingestion_runs`, `transactions`, `transactions_staging`, plus the `v_transactions_canonical` view. No `gateways`, `gateway_metrics`, `routing_policies`, `incidents`, `incident_evidence`, `simulations`, `simulation_results`, `recommendations`, `actions`, `verification_results`, or `audit_events` table exists. No anomaly detection, RCA, simulator, agent, Qwen/Ollama integration, or frontend code was added. No P2 item from the review was addressed.
