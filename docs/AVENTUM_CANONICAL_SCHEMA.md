# Aventum Canonical Schema

Normalized payment-event schema for future Aventum components, designed from the actual data established in [DATASET_INVENTORY.md](DATASET_INVENTORY.md), [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md), and [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md). Every field is classified `observed` (present as-is in `upi_transactions_2024`), `derived` (computed deterministically), `synthetic` (introduced by the infrastructure-simulation layer, no real basis), or `incident` (introduced by the controlled incident-injection layer, ground truth for evaluating Aventum only — never presented as historical fact). This document proposes the schema; **no database is initialized and no tables are created** — that happens in [DATABASE_DESIGN.md](DATABASE_DESIGN.md), also not executed in Day 1.

---

## Transaction

| Field | Type | Nullable | Description | Source dataset | Source column(s) | Transformation | Class |
|---|---|---|---|---|---|---|---|
| `transaction_id` | text (PK) | No | Unique transaction identifier | `upi_transactions_2024` | `transaction id` | Rename (strip space from source header) | observed |
| `timestamp` | timestamptz | No | Transaction time, second precision | `upi_transactions_2024` | `timestamp` | Parse to timestamptz; assume IST (unstated in source — see caveat in [DATA_DICTIONARY.md](DATA_DICTIONARY.md)) | observed |
| `amount` | numeric(12,2) | No | Transaction amount in INR | `upi_transactions_2024` | `amount (INR)` | Rename | observed |
| `status` | enum(SUCCESS, FAILED) | No | Transaction outcome | `upi_transactions_2024` | `transaction_status` | Rename | observed |

**Assumptions:** amount currency is INR (stated in the source column name, not independently verified). No `PENDING`/`TIMEOUT`/`REVERSED` status exists in source data — the enum is deliberately restricted to the two values actually observed; expanding it later requires new source evidence, not assumption.

## Payment Context

| Field | Type | Nullable | Description | Source dataset | Source column(s) | Transformation | Class |
|---|---|---|---|---|---|---|---|
| `payment_method` | enum(P2P, P2M, Bill Payment, Recharge) | No | Transaction category | `upi_transactions_2024` | `transaction type` | Rename | observed |
| `transaction_type` | text | No | Alias of `payment_method` retained for compatibility with the brief's field list | `upi_transactions_2024` | `transaction type` | Same value as `payment_method` | observed |
| `merchant_category` | text, nullable when N/A | Yes (logically, for P2P) | Merchant category | `upi_transactions_2024` | `merchant_category` | Rename; **set to NULL at load time for rows where `payment_method = 'P2P'`**, since no real merchant exists for those (finding in [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)) — this is a deliberate cleaning transformation, not present in the source | observed (nulled for P2P) |
| `region` | text | No | Sender's state | `upi_transactions_2024` | `sender_state` | Rename | observed (partial — 10 of 28+ states) |
| `device` | enum(Android, iOS, Web) | No | Sender device type | `upi_transactions_2024` | `device_type` | Rename | observed |
| `network` | enum(4G, 5G, WiFi, 3G) | No | Sender network type | `upi_transactions_2024` | `network_type` | Rename | observed |

**Constraint:** `merchant_category IS NULL` must hold whenever `payment_method = 'P2P'`; enforced at ETL time, not assumed by consumers.

## Banking / Issuer

| Field | Type | Nullable | Description | Source dataset | Source column(s) | Transformation | Class |
|---|---|---|---|---|---|---|---|
| `sender_bank` | text | No | Payer's bank (issuer/remitter) | `upi_transactions_2024` | `sender_bank` | Rename | observed |
| `receiver_bank` | text | No | Payee's bank (beneficiary) | `upi_transactions_2024` | `receiver_bank` | Rename | observed |
| `issuer_bank` | text | No | Canonical alias of `sender_bank`, per [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md) EXACT-FIELD mapping | `upi_transactions_2024` | `sender_bank` | Copy of `sender_bank`, kept as a separate canonical name to match the project brief's vocabulary | observed |
| `issuer_bank_full_name` | text | Yes | Legal entity name, for joining to NPCI reference files | *manual alias table* (audit-derived, `audit_scripts/deep_analysis.py` §E) | n/a | Lookup against a curated 8-row bank-alias table (6 auto-matched by substring, `SBI`→`State Bank Of India` and `PNB`→`Punjab National Bank` added manually — see [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §2) | derived |

## Infrastructure — **entirely synthetic; no source column exists for any field below**

| Field | Type | Nullable | Description | Source | Transformation | Class |
|---|---|---|---|---|---|---|---|
| `gateway_id` | text | No (once introduced) | Synthetic gateway/routing-endpoint identifier | none — invented | Introduced by the synthetic infrastructure layer; must never be presented as observed | **synthetic** |
| `routing_path` | text | Yes | Synthetic description of route taken | none — invented | Same | **synthetic** |
| `routing_policy` | text | Yes | Synthetic policy label active at transaction time | none — invented | Same | **synthetic** |
| `gateway_latency_ms` | integer | Yes | Synthetic processing latency | none — invented (no proxy anywhere in corpus, per [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)) | Modeled distribution, parameters TBD in a later design phase | **synthetic** |
| `gateway_response_code` | text | Yes | Synthetic granular response/error code | Proportions may be **calibrated** (not populated) using `npci_upi_remitter_banks`/`npci_upi_beneficiary_bank`/`npci_upi_payers_performance_psp` `BD%`/`TD%` split as a realistic bank-declined-vs-technical-declined ratio reference | Per-row values are still fully synthetic; only the aggregate proportion is proxy-informed | **synthetic** |
| `gateway_health_state` | enum | Yes | Synthetic point-in-time gateway health | none — invented | Same | **synthetic** |

## Derived Analytics — computed, never stored as an independent fact

| Field | Type | Nullable | Description | Formula | Source | Class |
|---|---|---|---|---|---|---|
| `rolling_success_rate` | numeric | Yes | Success rate over a trailing window | `AVG(status = 'SUCCESS')` over a defined trailing window, **excluding** the current row's own bucket if the window is small enough for self-inclusion to matter | `Transaction.status`, `Transaction.timestamp` | derived |
| `failure_rate` | numeric | Yes | `1 - rolling_success_rate` | — | derived |
| `volume` | integer | Yes | Transaction count over a window/segment | `COUNT(*)` | `Transaction` | derived |
| `gmv` | numeric | Yes | Gross merchandise value over a window/segment | `SUM(amount)` | `Transaction.amount` | derived |
| `error_rate` | numeric | Yes | Rate of non-SUCCESS outcomes over a window | `AVG(status != 'SUCCESS')` | `Transaction.status` | derived |
| `anomaly_score` | numeric | Yes | Deviation of a rolling metric from its own historical baseline | Statistical scoring method TBD (e.g. z-score against a longer trailing baseline) — deterministic, never LLM-computed, per the project's LLM-not-authoritative principle | derived (from `rolling_success_rate`/`volume`) | derived |
| `latency_metrics` (p50/p95/p99) | numeric | Yes | Percentile latency over a window | Percentile of `gateway_latency_ms` | **blocked until `gateway_latency_ms` (synthetic) exists** | derived-on-synthetic |

**Explicit warning carried from [DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md):** `rolling_success_rate`/`gmv`-style rolling aggregates must be computed as strictly trailing windows at query time. Do **not** reuse `upi_india_monthly_enriched`'s pre-baked `Volume_RollMean_3M`/`Value_RollMean_3M` columns as a template — those were shown to include the current period's own value, a leakage pattern this schema must not repeat.

## Incident Metadata — **entirely synthetic ground truth; no source column exists for any field below**

| Field | Type | Nullable | Description | Source | Class |
|---|---|---|---|---|---|
| `incident_id` | text (PK) | No (once introduced) | Unique incident identifier | none — invented by the incident-injection layer | **incident (synthetic ground truth)** |
| `incident_start` | timestamptz | No | Onset time | none — invented, chosen at injection design time | **incident** |
| `incident_end` | timestamptz | Yes | Resolution time (null while ongoing) | none — invented | **incident** |
| `incident_type` | text | No | e.g. `bank_decline_spike`, `technical_decline_spike` — categories informed by the real BD%/TD% split found in NPCI reference data ([FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)) | Category *shape* proxy-informed; specific instance is invented | **incident** |
| `affected_segment` | jsonb | No | The bank/device/state/category combination targeted by the injection | none — invented, chosen at injection design time | **incident** |
| `ground_truth_root_cause` | text | No | The true, known-by-construction cause, used only to evaluate whether Aventum's own diagnosis matches it | none — invented; **never shown to the diagnosis/simulation pipeline as input**, used only for offline evaluation | **incident** |

**Non-negotiable rule carried from [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §12:** `incident.*` fields are ground truth **for evaluating Aventum**, not facts Aventum is allowed to consume as input evidence — otherwise the evaluation is circular. Aventum's diagnosis pipeline sees only `Transaction`, `Payment Context`, `Banking/Issuer`, `Infrastructure`, and `Derived Analytics`; `Incident Metadata` is compared against its output afterward, out of band.

---

## Field-class summary

| Class | Count | Meaning |
|---|---|---|
| observed | 12 | Present as-is (or a direct rename/simple-filter of) a column in `upi_transactions_2024` |
| derived | 8 | Deterministically computed from observed or synthetic fields |
| synthetic | 6 | No source in any raw dataset; fully invented by the infrastructure-simulation layer, proportions may be calibrated against NPCI reference percentages but no per-row value is ever real |
| incident | 6 | Ground truth invented by the controlled incident-injection layer, used only for offline evaluation, never as pipeline input |

This 12/8/6/6 split is the schema-level expression of the finding already established in [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md): roughly a third of Aventum's canonical fields (Infrastructure + Incident Metadata) have zero grounding in any available dataset and must be built, not discovered.
