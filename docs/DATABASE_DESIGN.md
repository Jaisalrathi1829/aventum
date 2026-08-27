# Database Design

**Status (updated Day 2A):** the `transactions`, `banks`, `ingestion_runs`, `ingestion_rejects`, `transactions_staging`, and `dataset_registry` tables in this document **are now implemented and migrated** (Alembic revisions `0001`–`0002`, `backend/migrations/`), and the canonical transaction load has been executed against them. Every other table below (`gateways`, `gateway_metrics`, `routing_policies`, `incidents`, `incident_evidence`, `simulations`, `simulation_results`, `recommendations`, `actions`, `verification_results`, `audit_events`, `npci_reference_benchmarks`) remains a **design proposal only — not created**, and belongs to later phases. Field-level provenance (observed/derived/synthetic/incident) is defined once in [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) and referenced, not repeated, below.

Design is shaped directly by two Day 1 findings: (1) only `upi_transactions_2024` is transaction-grain and event-replayable ([DATASET_GRAIN_ANALYSIS.md](DATASET_GRAIN_ANALYSIS.md)), so it is the only table with expected cardinality in the hundreds-of-thousands; every other real dataset is reference/benchmark-grain and either stays a flat lookup table or is not persisted at all. (2) Infrastructure and Incident data are 100% synthetic ([FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)), so their tables must carry an explicit provenance flag rather than being indistinguishable from the real transaction table.

---

## `transactions`

**Purpose:** the fact table — one row per payment event, sourced from `upi_transactions_2024` at load time (Day 2+, not Day 1).

| Column | Type | Constraints |
|---|---|---|
| `transaction_id` | text | PRIMARY KEY |
| `timestamp` | timestamptz | NOT NULL |
| `amount` | numeric(12,2) | NOT NULL, CHECK (amount > 0) |
| `status` | text | NOT NULL, CHECK (status IN ('SUCCESS','FAILED')) |
| `payment_method` | text | NOT NULL |
| `merchant_category` | text | NULL (NULL when payment_method='P2P', enforced at load) |
| `region` | text | NOT NULL |
| `device` | text | NOT NULL |
| `network` | text | NOT NULL |
| `sender_bank` | text | NOT NULL, FK → `banks.bank_code` |
| `receiver_bank` | text | NOT NULL, FK → `banks.bank_code` |
| `fraud_flag` | boolean | NOT NULL DEFAULT false (retained for retrospective analysis only — see live-scoring caveat in [DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md)) |
| `source_dataset` | text | NOT NULL DEFAULT `'upi_transactions_2024'` — provenance tag |
| `transaction_type` | text | **GENERATED ALWAYS AS (`payment_method`) STORED**, NOT NULL — added Day 2A, see amendment below |
| `issuer_bank` | text | **GENERATED ALWAYS AS (`sender_bank`) STORED**, NOT NULL — added Day 2A, see amendment below |
| `ingestion_run_id` | bigint | NOT NULL, FK → `ingestion_runs.ingestion_run_id` — added Day 2A, provenance link |

> **Day 2A amendment — contradiction found and resolved.**
> [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) and [DATA_DICTIONARY.md](DATA_DICTIONARY.md) both define `transaction_type`, `issuer_bank`, and `issuer_bank_full_name` as canonical fields, but this table's original column list omitted all three. Implementing only the original list would have silently dropped fields the canonical schema promises to consumers.
> **Resolution (minimum justified change):** `transaction_type` and `issuer_bank` are added as PostgreSQL **generated** columns rather than ordinary duplicated ones. The canonical schema describes them as "same value as `payment_method`" and "copy of `sender_bank`", so generating them satisfies that wording exactly *and* makes divergence structurally impossible instead of merely validated. `issuer_bank_full_name` is **not** added here — `banks.legal_name` already holds that value and the canonical schema classes the field `derived`, so it is served by the `v_transactions_canonical` view (below) to keep one source of truth. `ingestion_run_id` was added to satisfy the Day 2A auditability requirement that every canonical row be traceable to the run that produced it. Full detail: [DAY2A_INGESTION_REPORT.md](DAY2A_INGESTION_REPORT.md) §Deviations.

**Indexes:** `(timestamp)`, `(sender_bank, timestamp)`, `(status, timestamp)`, `(region, timestamp)` — the four dimensions [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) confirmed are statistically usable for segmentation. Plus `(ingestion_run_id)` added Day 2A.

**View `v_transactions_canonical`** (added Day 2A): `transactions` LEFT JOIN `banks` on `issuer_bank = bank_code`, exposing `issuer_bank_full_name` without denormalizing it onto 250,000 rows.
**Expected cardinality:** ~250,000 rows for the Day 1 dataset as-is; designed to scale to millions once a live/larger feed replaces the static CSV.
**Relationships:** many-to-one to `banks` (both sender and receiver); one-to-many to `gateway_metrics` (once a transaction is assigned a synthetic gateway).

## `banks`

**Purpose:** dimension table for the 8 banks observed in `upi_transactions_2024`, extendable to the ~50–60 banks present in NPCI reference files.

| Column | Type | Constraints |
|---|---|---|
| `bank_code` | text | PRIMARY KEY (e.g. `SBI`, `HDFC`) |
| `legal_name` | text | NULL — populated only for the 8 banks with a confirmed manual alias to an NPCI legal name ([DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §2); NULL for any bank without a confirmed mapping, never guessed |
| `npci_reference_available` | boolean | NOT NULL DEFAULT false |

**Expected cardinality:** 8 rows initially (the transaction file's universe); up to ~60 if NPCI reference banks are loaded as unused-but-available dimension rows.
**Relationships:** one-to-many to `transactions` (as sender and receiver, two logical FKs to the same table — no separate junction needed).

## `dataset_registry` (added by the Day 2A P1-1 provenance fix, migration `0002`)

**Purpose:** the trusted binding between a source file's **content hash** and a dataset name. This is what makes `source_dataset` trustworthy: identity is resolved from SHA-256, never from filename. See [DAY2A_P1_FIX_REPORT.md](DAY2A_P1_FIX_REPORT.md).

| Column | Type | Constraints |
|---|---|---|
| `source_sha256` | char(64) | **PRIMARY KEY** — identity is keyed on content, not name. CHECK (length = 64) |
| `dataset_name` | text | NOT NULL, **UNIQUE** — a known name can never be rebound to different bytes. CHECK nonempty |
| `schema_version` | text | NOT NULL — the ingestion contract this binding was made under. CHECK nonempty |
| `source_filename` | text | NULL — **metadata only**, never consulted during identity resolution |
| `source_size_bytes` | bigint | NULL — metadata only |
| `registered_at` | timestamptz | NOT NULL DEFAULT now() |
| `registered_by` | text | NULL — audit |
| `notes` | text | NULL — audit |

**Seeded:** one row, `upi_transactions_2024` ↔ `8e46a45f…c89b6`, by the migration itself, so the canonical identity resolves from a clean migrate with no manual step.
**Expected cardinality:** one row per registered dataset; single digits.
**Relationships:** none enforced — `transactions.source_dataset` and `ingestion_runs.source_dataset` carry the resolved *name* rather than an FK, so a dataset can be de-registered without cascading into loaded history.

**Consequence for `transactions`:** the `source_dataset` server default (`'upi_transactions_2024'`) was **dropped** in migration `0002`. It was itself a hard-coded provenance value — an INSERT omitting the column silently acquired the canonical dataset's name. Provenance must now always be supplied explicitly from a resolved identity.

## `ingestion_runs` (added Day 2A)

**Purpose:** one row per ingestion attempt — the audit record proving exactly which source file produced which canonical rows. Never rewritten after completion; a retry creates a new run.

| Column | Type | Constraints |
|---|---|---|
| `ingestion_run_id` | bigserial | PRIMARY KEY |
| `source_file`, `source_filename` | text | NOT NULL |
| `source_sha256` | char(64) | NOT NULL, CHECK (length = 64) |
| `source_size_bytes` | bigint | NOT NULL |
| `source_dataset` | text | NOT NULL |
| `schema_version`, `code_version` | text | NOT NULL — the determinism inputs |
| `started_at` | timestamptz | NOT NULL DEFAULT now() |
| `finished_at`, `duration_seconds` | timestamptz / numeric(12,3) | NULL while running |
| `status` | text | NOT NULL, CHECK IN (`RUNNING`, `SUCCEEDED`, `FAILED`, `SKIPPED_IDEMPOTENT`) |
| `rows_read`, `rows_valid`, `rows_rejected`, `rows_inserted` | integer | NOT NULL DEFAULT 0, each CHECK >= 0 |
| `schema_drift_report`, `verification_report` | jsonb | NULL |
| `timestamp_assumption` | text | NULL — records that IST was an assumption, not a verified fact |
| `canonical_fingerprint` | char(64) | NULL — deterministic checksum of the canonical dataset |
| `error_message`, `notes` | text | NULL |

**Constraint of note:** `status = 'RUNNING' OR rows_valid + rows_rejected = rows_read` — a finished run must account for every row it read.
**Indexes:** `(source_sha256)` (idempotency lookup), `(status)`.
**Expected cardinality:** one row per run; tens to hundreds over the project's life.

## `ingestion_rejects` (added Day 2A)

**Purpose:** quarantine. Invalid records are never silently discarded — each rejection keeps the source row index, the failing rule, the responsible fields, and the raw payload so an engineer can reconstruct why the row did not become canonical.

| Column | Type | Constraints |
|---|---|---|
| `reject_id` | bigserial | PRIMARY KEY |
| `ingestion_run_id` | bigint | NOT NULL, FK → `ingestion_runs` |
| `source_row_index` | bigint | NOT NULL — 0-based, header excluded |
| `transaction_id` | text | NULL (may itself be the missing/invalid field) |
| `validation_error` | text | NOT NULL |
| `error_category` | text | NOT NULL |
| `offending_fields` | jsonb | NULL |
| `raw_record` | jsonb | NOT NULL — full source payload |
| `rejected_at` | timestamptz | NOT NULL DEFAULT now() |

**Expected cardinality:** 0 for the audited `upi_transactions_2024` source (measured); non-zero only if the source changes.

## `transactions_staging` (added Day 2A)

**Purpose:** the staging half of the atomic load. Carries the **same CHECK constraints** as `transactions` so a validation bug fails here — while the authoritative table is still untouched — rather than after promotion. Truncated at the start and end of every run; never read by downstream consumers.

**Expected cardinality:** 0 at rest; up to the full source row count mid-run.

## `npci_reference_benchmarks` (new table, justified by the audit — not in the brief's minimal list, added because the data supports it)

**Purpose:** hold the NPCI cross-sectional BD%/TD%/Approved% snapshot values, kept **structurally separate** from `transactions` per the join-risk finding in [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §2 (POSSIBLE BUT HIGH-RISK to join directly — never merged row-level into `transactions`).

| Column | Type | Constraints |
|---|---|---|
| `id` | serial | PRIMARY KEY |
| `bank_or_psp_name` | text | NOT NULL |
| `entity_type` | text | NOT NULL, CHECK (entity_type IN ('bank_remitter','bank_beneficiary','psp')) |
| `snapshot_period` | text | NULL — NULL when the source file stated no date (2 of 4 source files, per [DATASET_INVENTORY.md](DATASET_INVENTORY.md)); never inferred |
| `approved_pct` | numeric(5,2) | NULL |
| `bd_pct` | numeric(5,2) | NULL |
| `td_pct` | numeric(5,2) | NULL |
| `total_volume_mn` | numeric(14,2) | NULL |
| `source_file` | text | NOT NULL — provenance tag |

**Expected cardinality:** ~132 rows (50+50+32, per [DATASET_INVENTORY.md](DATASET_INVENTORY.md)); static reference data, not append-only.
**Relationships:** none enforced to `transactions` — deliberately not FK'd, to prevent accidental row-level join of a Sep-2023 snapshot onto 2024 transactions. Consumed only by the synthetic-parameter-calibration step, out of band from transaction queries.

## `gateways`

**Purpose:** dimension table for synthetic gateways — **entirely synthetic**, no real-world source (per [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)).

| Column | Type | Constraints |
|---|---|---|
| `gateway_id` | text | PRIMARY KEY |
| `display_name` | text | NOT NULL |
| `is_synthetic` | boolean | NOT NULL DEFAULT true, CHECK (is_synthetic = true) — this table only ever holds synthetic entities; the constraint documents that in the schema itself |
| `modeled_base_success_rate` | numeric(5,4) | NULL — the assumed parameter, set at synthetic-layer design time, never learned from `transactions` |

**Expected cardinality:** small (a handful of modeled gateways for demo purposes).

## `gateway_metrics`

**Purpose:** time-series synthetic health/latency observations per gateway — the `gateway_latency_ms`/`gateway_health_state` fields from the canonical schema, materialized as a time series rather than columns on `transactions`, so that gateway health can vary independently of any specific transaction.

| Column | Type | Constraints |
|---|---|---|
| `id` | bigserial | PRIMARY KEY |
| `gateway_id` | text | NOT NULL, FK → `gateways.gateway_id` |
| `timestamp` | timestamptz | NOT NULL |
| `latency_ms` | integer | NULL |
| `health_state` | text | NULL |
| `is_synthetic` | boolean | NOT NULL DEFAULT true |

**Indexes:** `(gateway_id, timestamp)`.
**Expected cardinality:** depends on the synthetic sampling interval chosen in a later design phase — not sized in Day 1.

## `routing_policies`

**Purpose:** synthetic routing-policy definitions used by the counterfactual simulation engine.

| Column | Type | Constraints |
|---|---|---|
| `policy_id` | text | PRIMARY KEY |
| `description` | text | NULL |
| `parameters` | jsonb | NULL — e.g. traffic-split rules, bounded by the deterministic safety/policy engine, not by the LLM |
| `is_synthetic` | boolean | NOT NULL DEFAULT true |

## `incidents`

**Purpose:** synthetic ground-truth incident records, per [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) — used for offline evaluation of Aventum's own diagnosis, never as pipeline input.

| Column | Type | Constraints |
|---|---|---|
| `incident_id` | text | PRIMARY KEY |
| `incident_start` | timestamptz | NOT NULL |
| `incident_end` | timestamptz | NULL |
| `incident_type` | text | NOT NULL |
| `affected_segment` | jsonb | NOT NULL |
| `ground_truth_root_cause` | text | NOT NULL |
| `is_synthetic` | boolean | NOT NULL DEFAULT true, CHECK (is_synthetic = true) |

**Relationships:** one-to-many to `incident_evidence`; conceptually one-to-many to `transactions` via `affected_segment` + time-range match (not an FK — the relationship is a query-time filter, since real transactions weren't labeled at ingest time with an incident ID).

## `incident_evidence`

**Purpose:** the Detect→Diagnose→Explain stage's supporting evidence for a given incident (before/after rates, affected volume, etc. — all `derived` per the canonical schema).

| Column | Type | Constraints |
|---|---|---|
| `id` | bigserial | PRIMARY KEY |
| `incident_id` | text | NOT NULL, FK → `incidents.incident_id` |
| `evidence_type` | text | NOT NULL (e.g. `before_after_success_rate`, `affected_volume`, `error_rate_change`) |
| `evidence_value` | jsonb | NOT NULL |
| `computed_at` | timestamptz | NOT NULL DEFAULT now() |

**Indexes:** `(incident_id)`.

## `simulations`

**Purpose:** one row per counterfactual simulation run (Simulate stage).

| Column | Type | Constraints |
|---|---|---|
| `simulation_id` | text | PRIMARY KEY |
| `incident_id` | text | NOT NULL, FK → `incidents.incident_id` |
| `routing_policy_id` | text | NOT NULL, FK → `routing_policies.policy_id` |
| `run_at` | timestamptz | NOT NULL DEFAULT now() |
| `assumptions` | jsonb | NOT NULL — explicit record of every synthetic parameter used, per the mandatory observed/simulated/assumption separation in [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §11 |

## `simulation_results`

**Purpose:** one row per candidate intervention size within a simulation run (supports "compare multiple intervention sizes").

| Column | Type | Constraints |
|---|---|---|
| `id` | bigserial | PRIMARY KEY |
| `simulation_id` | text | NOT NULL, FK → `simulations.simulation_id` |
| `traffic_pct` | numeric(5,2) | NOT NULL, CHECK (traffic_pct BETWEEN 0 AND 100) — value itself set/bounded by the deterministic policy engine |
| `projected_success_rate` | numeric(5,4) | NULL |
| `projected_recovered_transactions` | integer | NULL |
| `projected_gmv_impact` | numeric(14,2) | NULL |
| `confidence` | numeric(5,4) | NULL |
| `risk_score` | numeric(5,4) | NULL |

**Expected cardinality:** small per simulation (a handful of candidate traffic percentages per run).

## `recommendations`

**Purpose:** the bounded recovery recommendation surfaced for human approval (Recommend stage).

| Column | Type | Constraints |
|---|---|---|
| `recommendation_id` | text | PRIMARY KEY |
| `incident_id` | text | NOT NULL, FK → `incidents.incident_id` |
| `simulation_result_id` | bigint | NOT NULL, FK → `simulation_results.id` |
| `target_segment` | jsonb | NOT NULL |
| `target_gateway_id` | text | NULL, FK → `gateways.gateway_id` |
| `traffic_pct` | numeric(5,2) | NOT NULL, CHECK (traffic_pct BETWEEN 0 AND 100) |
| `duration_minutes` | integer | NOT NULL |
| `status` | text | NOT NULL DEFAULT `'pending_approval'`, CHECK (status IN ('pending_approval','approved','rejected','expired')) |
| `created_at` | timestamptz | NOT NULL DEFAULT now() |

## `actions`

**Purpose:** the executed action once approved (Execute stage) — deterministic, safety-bounded per the project's LLM-is-not-authoritative principle.

| Column | Type | Constraints |
|---|---|---|
| `action_id` | text | PRIMARY KEY |
| `recommendation_id` | text | NOT NULL, FK → `recommendations.recommendation_id` |
| `approved_by` | text | NOT NULL — human approver identity |
| `approved_at` | timestamptz | NOT NULL |
| `executed_at` | timestamptz | NULL |
| `rollback_triggered` | boolean | NOT NULL DEFAULT false |
| `rollback_reason` | text | NULL |

## `verification_results`

**Purpose:** Verify stage — post-action measurement against pre-action baseline.

| Column | Type | Constraints |
|---|---|---|
| `id` | bigserial | PRIMARY KEY |
| `action_id` | text | NOT NULL, FK → `actions.action_id` |
| `pre_action_baseline` | jsonb | NOT NULL |
| `post_action_outcome` | jsonb | NULL — **NULL for the Day 1 static-CSV prototype** (no live feed exists to observe a real post-action outcome against, per [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §G); populated only once a live feed or synthetic-continuation exists |
| `recovery_magnitude` | numeric | NULL |
| `recovery_speed_minutes` | integer | NULL |
| `effective` | boolean | NULL |
| `measured_at` | timestamptz | NULL |

## `audit_events`

**Purpose:** the full incident decision/audit trail (Audit stage) — append-only log spanning every prior table.

| Column | Type | Constraints |
|---|---|---|
| `event_id` | bigserial | PRIMARY KEY |
| `incident_id` | text | NULL, FK → `incidents.incident_id` |
| `event_type` | text | NOT NULL (e.g. `detected`, `diagnosed`, `simulated`, `recommended`, `approved`, `executed`, `verified`, `rolled_back`) |
| `event_payload` | jsonb | NOT NULL |
| `actor` | text | NOT NULL (`agent` or a human identity) |
| `occurred_at` | timestamptz | NOT NULL DEFAULT now() |

**Indexes:** `(incident_id, occurred_at)`.
**Constraint:** append-only at the application layer — no UPDATE/DELETE path is exposed for this table, since it is the audit trail itself.

---

## Deliberately not persisted as separate tables

- `upi_transaction_insights_dataset`, `upi_india_monthly_enriched` — per [DATA_PROVENANCE.md](DATA_PROVENANCE.md) §5, these are reference-only and low-confidence respectively; if ever loaded, they belong in a clearly-separated `reference_datasets` schema/namespace, not the operational tables above, and are out of scope for the Day 1 design.
- `npci_upi_apps_RAW`, `npci_year_wise_digital_transaction`, the 5 NPCI monthly product time-series files — no Aventum requirement in [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) depends on them; not modeled.
- A merged/joined "one big table" combining `transactions` with any NPCI file — explicitly rejected per the HIGH-RISK and INVALID join classifications in [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md).

No PostgreSQL table has been created for any of the items in this "deliberately not persisted" list. For the rest of the document, see the **Status** note at the top: the five Day 2A ingestion tables are implemented and migrated; every other table remains design-only.
