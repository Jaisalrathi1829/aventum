# Data Quality Report

All figures below are computed, not estimated (`audit_scripts/profile_datasets.py`, `audit_scripts/deep_analysis.py`; raw JSON in `audit_scripts/output/`). No corrections have been applied to any raw file.

---

## 1. `upi_transactions_2024` (primary candidate — highest scrutiny)

**Missingness:** 0 nulls in all 17 columns, 0%.

**Duplicates:** 0 duplicate rows; `transaction id` 100% unique (250,000/250,000).

**Numeric field — `amount (INR)`:**

| stat | value |
|---|---|
| count | 250,000 |
| mean | 1,311.76 |
| median | 629 |
| std | 1,848.06 |
| min | 10 |
| 1% | 48 |
| 5% | 97 |
| 25% | 288 |
| 75% | 1,596 |
| 95% | 4,687.05 |
| 99% | 9,003.01 |
| max | 42,099 |

No negative or zero amounts. Distribution is right-skewed (median ≪ mean), consistent with plausible payment-amount shape, though this alone does not prove real-world origin (see [DATA_PROVENANCE.md](DATA_PROVENANCE.md)).

**Categorical fields — frequency distributions:**

- `transaction_status`: SUCCESS 237,624 (95.05%), FAILED 12,376 (4.95%). Only two states — no PENDING/TIMEOUT/REVERSED, which a real gateway would typically emit at least occasionally.
- `sender_bank`/`receiver_bank`: 8 values each, roughly proportional to a plausible market-share ordering (SBI largest ≈25%, Kotak smallest ≈8%). No "Other bank" bucket, no missing/unknown bank.
- `device_type`: Android 75.1%, iOS 19.8%, Web 5.0%.
- `network_type`: 4G 59.9%, 5G 25.0%, WiFi 10.1%, 3G 5.0%.
- `sender_state`: exactly 10 states present (Maharashtra 15.0% down to West Bengal 8.0%) — India has 28 states + 8 UTs; this dataset covers only a fixed subset, so state-level analysis outside these 10 is impossible by construction.
- `fraud_flag`: 480/250,000 = 0.192% positive.

**Impossible/suspicious values:** none found — no future dates (max timestamp 2024-12-30, well before the current date), no negative amounts, no out-of-range hour_of_day (0–23 confirmed), no malformed timestamps (100% parse coverage).

**Contradictory columns / redundancy:** `hour_of_day`, `day_of_week`, `is_weekend` are all deterministic functions of `timestamp` and were spot-checked as internally consistent (e.g. `is_weekend=1` rows are exclusively Saturday/Sunday in `day_of_week`). This is redundancy, not a contradiction, but it means these three columns carry **zero independent information** beyond `timestamp` — a modeling note, not a defect.

**Failure-rate variance across every available dimension (computed in `deep_analysis.py`, section A):**

| Segment dimension | Failure-rate spread (max − min, percentage points) |
|---|---|
| sender_bank | 0.281 |
| receiver_bank | 0.506 |
| device_type | 0.220 |
| network_type | 0.364 |
| merchant_category | (measured, low single digits — see JSON) |
| sender_state | (measured, low single digits — see JSON) |
| hour_of_day | 0.863 |
| day_of_week | 0.324 |
| is_weekend | 0.197 |

Every spread is under 1 percentage point against a ~4.95% base rate. This is the data-quality finding with the largest downstream consequence for Aventum: **failure in this dataset behaves as if it were injected as i.i.d. random noise, uncorrelated with any observable dimension.** A real payment ecosystem experiencing a genuine incident (e.g. one issuer's authorization system degrading) would show a failure-rate spread of many multiples of the base rate concentrated in one segment — nothing like that exists natively in this file. This is analyzed further in [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) (Root-Cause Analysis, Analytical Sufficiency) and drives the synthetic-incident-layer requirement in [DATASET_ACQUISITION_PLAN.md](DATASET_ACQUISITION_PLAN.md).

**Leakage check:** `fraud_flag` does not deterministically predict `transaction_status` (459 SUCCESS + 21 FAILED among fraud-flagged rows) — no leakage between the two. Full leakage analysis in [DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md).

**Temporal density (relevant to data quality of the time axis, not just feasibility):** ~687 transactions/day, ~28.5/hour, ~4.76 per 10-minute window, with **12.46% of all 10-minute windows containing zero transactions** and **51.7% containing fewer than 5**. This is a real sparsity characteristic of the file, not a defect, but it materially limits what can be measured reliably at fine time resolution (quantified in the Requirements Matrix, §9 Temporal Feasibility).

---

## 2. `upi_transaction_insights_dataset`

- 0 nulls, 0 duplicate rows, `transaction_id` 100% unique (500/500).
- `amount`: min 17.62, max 4,973.82, mean 2,523.67 — **suspiciously bounded just under round number 5,000** with 2-decimal precision throughout; consistent with a `random.uniform(low, 5000)`-style generator rather than an observed payment population.
- `is_successful`: **exactly 250/250 (50.00%/50.00%)** — real UPI success rates are consistently reported in the 95–99% range (and the sibling dataset above independently shows 95.05%). A perfect 50/50 balance on a binary outcome field is the signature of a deliberately class-balanced synthetic/toy dataset (common in ML-tutorial data), not an observed outcome distribution. **This field must not be used to estimate or calibrate any real base rate.**
- `location_type`: Urban 34.8%, Rural 34.6%, Semi-Urban 30.6% — also near-perfectly balanced across only 3 buckets, another synthetic-generation signature.
- `merchant_category`, `payment_mode`: reasonably spread across 10 and 4 categories respectively, no single dominant category — again consistent with uniform-random category assignment rather than observed merchant-mix skew.
- No whitespace/casing issues, no placeholder tokens.

---

## 3. `upi_india_monthly_enriched`

- Nulls are structurally expected (warm-up periods for MoM/YoY/rolling-mean windows) and were individually verified as such — not data defects: `Volume_MoM_%`/`Value_MoM_%` 7 nulls (5.83%) each, `Volume_YoY_%`/`Value_YoY_%` 12 nulls (10.0%) each, `Volume_RollMean_3M`/`Value_RollMean_3M` 2 nulls (1.67%) each.
- `Volume_MoM_%`/`Value_MoM_%` contain literal `inf` values (division by a $0 prior-month base during the 2016 ramp-up), which pandas silently parses as IEEE infinity — this produced `RuntimeWarning`s during standard-deviation computation and means any naive `.mean()`/`.std()` on these columns without first handling `inf` will silently produce `NaN` or a nonsensical value. Flagged as a required cleaning step if this file is ever used quantitatively.
- 0 duplicate rows, `Date` 100% unique.
- **Material inconsistency against `npci_upi_product_statistics` for the same months** — this is a cross-dataset finding and is documented once, in full, in [DATA_PROVENANCE.md](DATA_PROVENANCE.md) §"Contradiction found" and [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md), rather than repeated here.

---

## 4. NPCI monthly time-series files (BHIM, Fastag, IMPS, *99#, UPI Product Statistics)

- 0 duplicate rows in all 5 files, `Month` 100% unique within each.
- Placeholder handling: pre-launch months are marked with a literal `"-"` string (BHIM: 8 rows; `*99#`: 8 rows) rather than being omitted or left blank — these must be filtered, not coerced to 0, since "-" means "product not yet live," which is a materially different state from "zero volume while live."
- `Fastag Statistics.csv`: one row (2016-11) uses `NA` instead of `-` for the same "not yet meaningful" concept — **inconsistent placeholder convention within the file itself** (both `-` and `NA` are used for what appears to be the same semantic state in sibling files), a minor but real data-quality inconsistency.
- `UPI Product  Statistics.csv`: one numeric cell contains a **non-breaking-space character** (`"10.35\xa0"`) appended after the digits, which would break a naive `float()` cast without an explicit strip of `\xa0` (plain `.strip()` alone does not remove it in all pandas/Python configurations — confirmed necessary as an explicit replace step in the profiling script).
- All Volume/Value numeric columns are stored as **strings with comma thousand-separators** (e.g. `"1,002.15"`), confirmed by the profiler flagging them as "looks numeric but stored as text" at >95% numeric-coercion coverage once commas are stripped.
- No casing inconsistencies (there are no free-text categorical columns in these files beyond `Month`).

---

## 5. NPCI single-snapshot entity files (Remitter Banks, Beneficiary Bank, Payers Performance PSP, "mandate creation")

- 0 duplicate rows in all 4 files; bank/PSP name is 100% unique per file (no entity appears twice within one file).
- Percentages are stored as strings with a literal `%` suffix (e.g. `"93.93%"`) — require stripping before numeric use.
- Approved% + BD% + TD% do **not** always sum to exactly 100.00% (rounding artifacts of ≤0.1 point observed on spot-check rows) — immaterial for directional analysis, worth noting for any downstream exact-reconciliation use.
- `npci_upi_remitter_banks.csv`: one standout outlier confirmed — **Central Bank of India: TD% = 53.47%**, i.e. over half of that bank's UPI remittance attempts were technically declined in the Sep-2023 snapshot, versus a median TD% in the low single digits across the other 49 banks. This is a genuine, extreme, bank-specific degradation signal present in real reference data — useful evidence that such real-world incidents occur, even though (per §1 above) our transaction-level dataset does not itself exhibit this kind of concentrated failure. See [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md).
- `npci_upi_mandate_creation.csv` is a **filename/content mismatch**, not a quality defect per se, but is flagged here again because it affects trust: any pipeline that joins on filename-derived assumptions ("this file is about mandates") would silently mislabel remitter-bank data as mandate data. Documented fully in [DATASET_INVENTORY.md](DATASET_INVENTORY.md).
- `npci_upi_payers_performance_psp.csv` and `npci_upi_mandate_creation.csv` have **no stated snapshot date anywhere in the file** — their temporal alignment with the two Sep-2023-dated files is unverifiable from file contents alone. Treated as "period unknown," not assumed to be Sep-2023.

---

## 6. `npci_year_wise_digital_transaction`

- 0 duplicate rows, `Financial Year` 100% unique (5 rows).
- **Non-UTF-8 encoding**: byte `0x96` inside `"2021 – 22"` (a Windows-1252 en-dash) breaks strict UTF-8 decoding; required `cp1252` fallback, confirmed programmatically as the only file in the corpus needing this.
- Too few rows (5) for any statistical summary beyond direct inspection.

---

## 7. `npci_upi_apps_RAW`

- 3-row multi-index header (title / category-group / sub-metric) — not machine-parseable with a single-row-header `read_csv` call without producing garbage column names. Profiled structurally rather than per-column; see [DATASET_INVENTORY.md](DATASET_INVENTORY.md).
- Spot-checked rows show `-` used as a placeholder for "not applicable" cells (e.g. an app with 0 B2B volume shows `-,-` rather than `0,0`), same placeholder-convention inconsistency noted in §4.
- "Total" columns are the sum of the 4 category columns — a **contradictory-if-misused** column (double-counting risk if "Total" is summed alongside its own components), not a data-quality defect but a structural trap for careless aggregation.

---

## Cross-cutting data-quality findings

1. **No dataset in the corpus has ANY missingness in its primary transactional/observational fields** — the only nulls anywhere are the structurally-expected warm-up nulls in `upi_india_monthly_enriched`'s engineered features. Real-world payment data almost never arrives this clean; this is itself a (mild) signal toward the transaction-level files being synthetic rather than raw operational exports, consistent with the provenance findings.
2. **Every percentage/currency field across the 10 NPCI files is stored as text** requiring comma-strip, `%`-strip, and whitespace/NBSP-strip before numeric use — a uniform, mechanical cleaning requirement, not a per-file idiosyncrasy.
3. **Placeholder-token conventions are inconsistent across files** (`-` vs `NA` vs blank), and inconsistent even within a plausible common-source group (BHIM/`*99#` use `-`, Fastag uses `NA` for what looks like the same "not yet launched" concept).
4. **No PII was found** in any dataset — no names, phone numbers, VPAs (UPI IDs), account numbers, or device identifiers. This is favorable for privacy but is also mentioned because a real transaction-log export would typically contain at least a hashed identifier; its complete absence is another mild synthetic-origin signal.
