# Data Dictionary

Formal vocabulary for every canonical field defined in [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md). Types, sources, and transformations are authoritative there and not repeated in full here — this document focuses on **meaning, allowed values, and caveats a consumer must know before using the field**.

---

### `transaction_id`
**Meaning:** unique identifier of one payment event. **Allowed values:** any string matching source format `TXN0000000001`. **Source:** `upi_transactions_2024.transaction id`. **Class:** observed. **Caveat:** confirmed 100% unique across 250,000 rows; safe as a primary key.

### `timestamp`
**Meaning:** moment the transaction occurred. **Allowed values:** 2024-01-01 00:05:10 to 2024-12-30 23:55:40. **Class:** observed. **Caveat:** timezone is unstated in the source file; assumed IST (India) given the dataset's Indian-UPI context, not independently confirmed. 99.44% unique — collisions (multiple transactions in the same second) are expected at this volume, not a data defect.

### `amount`
**Meaning:** transaction value in INR. **Allowed values:** 10–42,099 (integer in source, cast to numeric(12,2)). **Class:** observed. **Caveat:** currency is inferred from the source column name `amount (INR)`, not from a separate currency field — there is no multi-currency support anywhere in the corpus.

### `status`
**Meaning:** transaction outcome. **Allowed values:** `SUCCESS`, `FAILED` only. **Class:** observed. **Caveat:** no `PENDING`/`TIMEOUT`/`REVERSED` state exists in source data; do not assume the enum covers states a real gateway might emit — it reflects only what this dataset contains.

### `payment_method` / `transaction_type`
**Meaning:** category of UPI transaction. **Allowed values:** `P2P`, `P2M`, `Bill Payment`, `Recharge`. **Source:** `upi_transactions_2024."transaction type"`. **Class:** observed. **Caveat:** none.

### `merchant_category`
**Meaning:** category of merchant involved. **Allowed values:** 10 categories (Grocery, Food, Shopping, Fuel, Other, Utilities, Transport, Entertainment, Healthcare, Education) when `payment_method != 'P2P'`; **NULL when `payment_method = 'P2P'`** (canonical-schema cleaning rule — see [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md)). **Class:** observed, nulled for P2P. **Caveat:** the raw source populates this field even for P2P rows; that raw value must be discarded, not trusted, for those rows (45% of the dataset) — see [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md).

### `region`
**Meaning:** sender's Indian state. **Allowed values:** exactly 10 states (Maharashtra, Uttar Pradesh, Karnataka, Tamil Nadu, Delhi, Telangana, Gujarat, Andhra Pradesh, Rajasthan, West Bengal). **Source:** `sender_state`. **Class:** observed (partial). **Caveat:** no receiver-side geography exists; India has 28 states + 8 union territories, so any claim using this field is scoped to these 10 only, never "nationwide."

### `device`
**Meaning:** sender's device platform. **Allowed values:** `Android`, `iOS`, `Web`. **Class:** observed. **Caveat:** none.

### `network`
**Meaning:** sender's network connection type. **Allowed values:** `4G`, `5G`, `WiFi`, `3G`. **Class:** observed. **Caveat:** none.

### `sender_bank` / `issuer_bank`
**Meaning:** the payer's bank (issuer/remitter in UPI terminology). **Allowed values:** exactly 8 — `SBI`, `HDFC`, `ICICI`, `IndusInd`, `Axis`, `PNB`, `Yes Bank`, `Kotak`. **Class:** observed. **Caveat:** these are informal short names, not legal entity names; India's real UPI ecosystem has ~50–60 participating banks per NPCI reference files — this dataset covers a fixed 8-bank subset only.

### `receiver_bank`
**Meaning:** the payee's bank (beneficiary). **Allowed values:** same 8-bank set as `sender_bank`. **Class:** observed. **Caveat:** same as above.

### `issuer_bank_full_name`
**Meaning:** legal entity name, for cross-referencing NPCI files. **Allowed values:** populated only for the 8 banks with a confirmed alias (6 via automatic substring match, `SBI`→`State Bank Of India` and `PNB`→`Punjab National Bank` added manually — see [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §2). **Class:** derived. **Caveat:** NULL for any bank without a confirmed mapping — never guessed or fuzzy-matched beyond the tested alias table.

### `fraud_flag`
**Meaning:** whether the transaction was flagged as fraudulent. **Allowed values:** boolean, 0.192% positive (480/250,000). **Class:** observed. **Caveat:** timing of when this flag is assigned relative to `status` is unstated in the source; per [DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md), treat as a post-hoc label — do not use as a live-scoring input feature.

### `gateway_id`, `routing_path`, `routing_policy`, `gateway_latency_ms`, `gateway_response_code`, `gateway_health_state`
**Meaning:** the entire payment-infrastructure layer Aventum needs to reason about gateway/routing behavior. **Allowed values:** to be defined at synthetic-layer design time. **Class:** **synthetic — no source column or proxy exists in any of the 14 profiled datasets** (confirmed by exhaustive column-name search, [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)). **Caveat:** `gateway_response_code`'s aggregate proportions (not per-row values) may be calibrated against the real `BD%`/`TD%` split found in `npci_upi_remitter_banks`/`npci_upi_beneficiary_bank`/`npci_upi_payers_performance_psp` — this is a proportion reference only, never a per-transaction source of truth. **These fields must always be visibly labeled synthetic wherever displayed.**

### `rolling_success_rate`, `failure_rate`, `volume`, `gmv`, `error_rate`, `anomaly_score`, `latency_metrics`
**Meaning:** deterministic analytics computed over `transactions`. **Class:** derived. **Caveat:** must be computed as strictly trailing windows at query time — do not model these after `upi_india_monthly_enriched`'s pre-baked rolling-mean columns, which were found to include the current period's own value (leakage pattern, [DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md)). `latency_metrics` is blocked until synthetic `gateway_latency_ms` exists.

### `incident_id`, `incident_start`, `incident_end`, `incident_type`, `affected_segment`, `ground_truth_root_cause`
**Meaning:** controlled synthetic incident ground truth, used to evaluate Aventum's own detection/diagnosis. **Class:** **incident (synthetic ground truth)** — no dataset in the corpus marks any real time window as a labeled incident ([AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §12). **Caveat:** `incident_type`'s category *shape* (e.g. bank-decline vs technical-decline) is informed by the real BD%/TD% taxonomy in NPCI reference files, but each specific incident instance is invented. **These fields must never be fed into Aventum's diagnosis pipeline as input — they exist only for offline evaluation of its output**, otherwise evaluation becomes circular.

---

## Reference-only fields (not part of the canonical `transactions`-centric schema, but cited elsewhere in this audit)

| Field | Dataset | Meaning | Caveat |
|---|---|---|---|
| `Volume_Mn`, `Value_Cr` | `npci_upi_product_statistics` | National monthly UPI volume/value | Preferred over `upi_india_monthly_enriched`'s equivalent columns — see [DATA_PROVENANCE.md](DATA_PROVENANCE.md) §3 for the material contradiction between the two. |
| `BD%`, `TD%`, `Approved%` | `npci_upi_remitter_banks`, `npci_upi_beneficiary_bank`, `npci_upi_payers_performance_psp` | Bank-declined / technical-declined / approved percentage | Single cross-sectional snapshot (Sep-2023 for 2 of the 4 source files, unstated for the other 2) — a calibration reference, never a per-transaction or per-current-period fact. `Approved% + BD% + TD%` are 2 independent degrees of freedom, not 3 (they sum to ~100%). |
| `is_successful` | `upi_transaction_insights_dataset` | Binary success label | **Do not use to estimate any real base rate** — confirmed as an artificial, perfectly balanced 50/50 split, not representative of real UPI success rates ([DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md)). |
| `Event_Code` | `upi_india_monthly_enriched` | Unexplained categorical code | No legend exists anywhere in the corpus for what each code value means — treat as unverified, do not use in any model or explanation until confirmed ([DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md)). |

No field in this dictionary was invented without a documented source, transformation, or explicit synthetic/incident classification.
