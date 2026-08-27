# Dataset Inventory

Status: computed from actual files under `data/raw/` using `audit_scripts/profile_datasets.py` (pandas-based, read-only). Raw JSON profiles for every tabular file live in `audit_scripts/output/*.json` and back every number in this document.

All files were inspected. `data/raw/` contains **22 files** across **4 source folders**: 12 CSV files (11 genuine CSV + 1 file named `.xlsx.csv` that is actually plain CSV), 1 Markdown README, 1 Power BI `.pbix` binary, 4 raster images, and 1 MP4 video. There are **no** Excel, JSON, Parquet, or SQL/dump files despite the task checklist allowing for them.

---

## 1. Non-tabular / non-data files (inspected, not profiled numerically)

| File | Folder | Type | Size | Notes |
|---|---|---|---|---|
| `README.md` | UPI TRANSACTION DATASET | Markdown | 3,369 B | Only documentation file in all of `data/raw/`. Describes a Power BI dashboard project (see [DATA_PROVENANCE.md](DATA_PROVENANCE.md)). |
| `UPI_DashBoard.pbix` | UPI TRANSACTION DATASET | Power BI binary (zip container) | 9,333,912 B | Not opened — `.pbix` is a proprietary zip-based container; pandas/Python cannot read its embedded data model without a dedicated library (e.g. Power BI Desktop, `pbixray`) that is out of scope for this audit. Its **row-level data is therefore not part of our inspectable raw dataset**, even though the file physically sits in `data/raw/`. |
| `UPI_Project -comp-Video.mp4` | UPI TRANSACTION DATASET | Video | 19,345,686 B | Demo/walkthrough video. No tabular content. |
| `Banks_states.jpg`, `Merchant.jpg`, `Time_trends.jpg`, `Transactions.jpg`, `upi cover Image.png`, `Home (2).png` | UPI TRANSACTION DATASET | Images | 92.7 KB – 1.85 MB (6 files) | Dashboard screenshots/cover art. Used only as descriptive/provenance evidence, not as data. |

**Consequence:** the entire "UPI TRANSACTION DATASET" folder contributes **zero directly usable rows**. Its only audit value is the README text and the (unconfirmed) circumstantial link documented in [DATA_PROVENANCE.md](DATA_PROVENANCE.md).

---

## 2. Tabular files — full profile

Encoding: UTF-8 unless noted. Delimiter: comma for all files. Header row: single row unless noted.

### 2.1 `Indian UPI Transactions/upi_india_monthly_enriched.csv`

- Rows: **120**, Columns: **18**, Duplicate rows: 0, Size: 17,673 B
- Grain: one row per **calendar month**, national ecosystem level (see [DATASET_GRAIN_ANALYSIS.md](DATASET_GRAIN_ANALYSIS.md))
- Columns: `Date, Year, Month_Num, Month_Name, Financial_Year, Volume_Mn, Value_Cr, Volume_MoM_%, Value_MoM_%, Volume_YoY_%, Value_YoY_%, Month_Sin, Month_Cos, Volume_RollMean_3M, Value_RollMean_3M, Is_Covid_Period, Is_Festive_Season, Event_Code`
- Date range: **2016-01-01 to 2025-12-01** (100% coverage, monthly granularity), candidate PK: `Date` (100% unique)
- Nulls: `Volume_MoM_%`/`Value_MoM_%` 7 nulls each (5.83%, expected — no prior month for row 1 and a few zero-volume-denominator months), `Volume_YoY_%`/`Value_YoY_%` 12 nulls each (10.0%, expected — first 12 months have no prior year), `Volume_RollMean_3M`/`Value_RollMean_3M` 2 nulls each (1.67%, expected warm-up window). All other columns 0 nulls.
- Suspicious values: `Volume_MoM_%` and `Value_MoM_%` contain **`inf`** values (division-by-zero from a $0 → nonzero month transition, e.g. 2016-08). `pandas` parses the literal token `inf` in the CSV as IEEE `inf`, confirmed by triggering `RuntimeWarning: invalid value encountered in subtract` during `std()` computation.
- This file is already a **derived/enriched** artifact (rolling means, sin/cos month encoding, MoM/YoY %, binary event flags) — not raw NPCI output. See §5 below and [DATA_PROVENANCE.md](DATA_PROVENANCE.md) for the material discrepancy found against `npci_upi_product_statistics` for overlapping months.

### 2.2 `UPI Transaction Insights Dataset/upi_transaction_insights_dataset.csv`

- Rows: **500**, Columns: **8**, Duplicate rows: 0, Size: 32,059 B
- Grain: one row per **payment transaction**
- Columns: `transaction_id, date, time, amount, merchant_category, payment_mode, location_type, is_successful`
- `transaction_id` format `TXN1000xx`, 100% unique, candidate PK
- Date range: 2024-01-01 to 2024-12-30 (100% coverage, day granularity); `time` is a separate free `HH:MM` string column (not combined with `date` into one timestamp) — 422 distinct values, no seconds precision
- `amount`: float, min 17.62, max 4973.82, mean 2523.67 — **capped under 5,000**, no value in the sample exceeds it. Two-decimal precision throughout.
- `is_successful`: exactly **250 "Yes" / 250 "No"** — a perfect 50.0%/50.0% split (see §3, this is a strong synthetic-data signal, not a plausible real-world UPI success rate)
- No nulls, no duplicate rows, no placeholder tokens found in any column
- No bank field, no gateway/error field, no device field

### 2.3 `UPI Transactions 2024 Dataset/upi_transactions_2024.csv`

- Rows: **250,000**, Columns: **17**, Duplicate rows: 0, Size: 29,811,789 B (the largest raw file)
- Grain: one row per **payment transaction**
- Columns (note irregular naming — spaces, not underscores, in two of them): `transaction id, timestamp, transaction type, merchant_category, amount (INR), transaction_status, sender_age_group, receiver_age_group, sender_state, sender_bank, receiver_bank, device_type, network_type, fraud_flag, hour_of_day, day_of_week, is_weekend`
- `transaction id`: format `TXN0000000001` (13-char, zero-padded), **100% unique**, confirmed primary key
- `timestamp`: full `YYYY-MM-DD HH:MM:SS`, 100% coverage, range **2024-01-01 00:05:10 to 2024-12-30 23:55:40** (364-day span, no Dec 31/Jan effectively single calendar year)
- `amount (INR)`: int64, min 10, max 42,099, mean 1,311.76, median 629 — right-skewed, no negative or zero values
- `transaction_status`: **SUCCESS 237,624 (95.05%) / FAILED 12,376 (4.95%)** — no other status values (no PENDING, no REVERSED, no TIMEOUT)
- 8 distinct `sender_bank`/`receiver_bank` values: SBI, HDFC, ICICI, IndusInd, Axis, PNB, Yes Bank, Kotak — short-form names, not legal entity names (see [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) for normalization against NPCI's legal-name files)
- `device_type` (Android/iOS/Web), `network_type` (4G/5G/WiFi/3G), `transaction type` (P2P/P2M/Bill Payment/Recharge), `merchant_category` (10 categories), `sender_state` (10 Indian states only — not all 28+ states/UTs)
- `fraud_flag`: binary, mean 0.192% (480 flagged rows) — present in both SUCCESS (459) and FAILED (21) rows, i.e. not a deterministic proxy for failure (see [DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md))
- `hour_of_day`, `day_of_week`, `is_weekend` are all **exact derivatives of `timestamp`** (redundant but internally consistent — not independent observed fields)
- No nulls anywhere, no duplicate rows, no placeholder tokens
- **No gateway, routing, error-code, or latency field of any kind.**
- Cross-checked numerically against the README of the unrelated "UPI TRANSACTION DATASET" folder — see [DATA_PROVENANCE.md](DATA_PROVENANCE.md) for a strong (but unconfirmed) circumstantial match.

### 2.4 NPCI ecosystem statistics — `NPCI Products Statistics Since Launch/` (10 files)

All 10 files are **national/entity-level aggregate statistics**, not transaction-level. None carries a citation, source URL, or collection-methodology note (see [DATA_PROVENANCE.md](DATA_PROVENANCE.md)). Grouped by shape:

**(a) Monthly national time series (5 files)** — one row per calendar month, columns `Month` (format `YY-Mon`, e.g. `23-Aug`) + 2–3 numeric measures. Pre-launch months are marked with literal `"-"` placeholders.

| File → dataset name | Rows | Date range (parsed) | Columns | Placeholder cells |
|---|---|---|---|---|
| `BHIM product Statistics.csv` → `npci_bhim_statistics` | 91 | 2016-04 to 2023-10 | Month, Banks live, Volume(Mn), Value(Cr) | 8 rows all-`-` (pre-launch, Apr–Nov 2016) |
| `Fastag Statistics.csv` → `npci_fastag_statistics` | 85 | 2016-11 to 2023-11 | Month, Banks live, Tag Issuance(Nos.), Volume(Mn), Amount(Cr) | 1 row (`NA`,`NA` for Nov-2016 volume/amount) |
| `IMPS Statistics.csv` → `npci_imps_statistics` | 122 | 2013-09 to 2023-10 | Month, Member Banks, No. of Transactions(Mn), Amount(Cr) | none observed |
| `PS99.xlsx.csv` → `npci_star99_statistics` (`*99#` USSD banking) | 91 | 2016-04 to 2023-10 | Month, Banks live, Volume(Mn), Value(Cr) | 8 rows all-`-` (pre-launch) |
| `UPI Product  Statistics.csv` (double space in filename) → `npci_upi_product_statistics` | 92 | 2016-04 to 2023-11 | Month, Banks live on UPI, Volume(in Mn), Value(in Cr.) | none observed; one cell (`10.35\xa0`, row for a mid-series month) contains a **non-breaking-space character** appended to a numeric string — a malformed-value finding |

Thousand-separators are comma-formatted and quoted (e.g. `"1,002.15"`), requiring de-comma-ing before numeric use — confirmed by the profiler auto-detecting these Volume/Value columns as **object/string dtype even though they are >95% numeric-coercible** ("looks numeric but stored as text").

**(b) Single-snapshot, entity-grain files (3 files)** — one row per bank or PSP, for **one unstated or partially-stated point in time**, not a time series.

| File → dataset name | Rows | Entity | Snapshot period stated in header? | Columns |
|---|---|---|---|---|
| `UPI Remitter Banks.csv` → `npci_upi_remitter_banks` | 50 | Bank | Yes — "Sep-2023" in header | Sr.No, Bank, Total Volume(Mn), Approved%, BD%, TD%, Total Debit Reversal Count(Mn), Debit Reversal Success% |
| `UPI Beneficiary Bank.csv` → `npci_upi_beneficiary_bank` | 50 | Bank | Yes — "Sep-2023" in header | Sr.No, Bank, Total Volume(Mn), Approved%, BD%, TD%, Deemed Approved% |
| `UPI Payers Perforance PSP.csv` → `npci_upi_payers_performance_psp` | 32 | PSP | **No date in header at all** | Sr.No, Payer PSP, Total Volume(Mn), Approved%, BD%, TD% |

`BD%` = Bank Declined percentage, `TD%` = Technical Declined percentage — this is a genuine (if coarse) **root-cause taxonomy already present in reference data**; see [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md).

**(c) Filename/content mismatch (1 file) — flagged, not silently corrected**

`UPI mandate creation.csv` → `npci_upi_mandate_creation`. **The filename claims UPI mandate-creation statistics** (mandates are recurring-payment authorizations — a materially different concept with its own success/failure semantics). **The actual column header is `Sr. No., Remitter Bank, Total Volume, Approved%, BD%, TD%`** — structurally identical to a remitter-bank approval snapshot, not mandate data, and its values differ from `UPI Remitter Banks.csv` (e.g. SBI `Total Volume` = `6,77,064` here vs `2930.95` "In Mn" there — different units/snapshot, not a duplicate). No period is stated anywhere in the file. **This file must not be used as evidence about UPI mandates.** It is treated in this audit as an unlabeled/undated remitter-bank snapshot of unknown period, distinct from the two dated ones above.

**(d) Yearly, multi-instrument comparison (1 file)**

`Year wise Digital Transaction.csv` → `npci_year_wise_digital_transaction`. 5 rows (FY2018-19 … FY2022-23 partial), 11 columns comparing Volume/Value across Cards, UPI, IMPS, RTGS, NEFT. **Encoding is not UTF-8** — contains byte `0x96` (a Windows-1252 en-dash `–` inside `"2021 – 22"`); the file required `cp1252` fallback decoding, confirmed programmatically. This is the only non-UTF-8 file in the corpus.

**(e) Irregular multi-row header (1 file) — profiled separately as raw**

`UPI Apps.csv` → `npci_upi_apps_RAW`. 73 data rows × 12 columns, but the **header spans 3 rows**: row 1 is a title (`UPI Apps (Oct'23)`), row 2 names 5 transaction-category groups (Customer Initiated / B2C / B2B / On-us / Total), row 3 names the Volume/Value sub-metric under each group. A naive single-header `pd.read_csv` would silently misparse this file — it was profiled with `header=None` and documented structurally rather than column-by-column. Entity grain: one row per PSP app (~70 apps), snapshot Oct-2023.

---

## 3. Summary table (all 14 profiled tabular datasets)

| Dataset name | Source file | Rows | Cols | Grain | Time range | Dup rows |
|---|---|---|---|---|---|---|
| `upi_transactions_2024` | UPI Transactions 2024 Dataset | 250,000 | 17 | transaction | 2024-01-01→2024-12-30 (sec) | 0 |
| `upi_transaction_insights_dataset` | UPI Transaction Insights Dataset | 500 | 8 | transaction | 2024-01-01→2024-12-30 (day+separate time) | 0 |
| `upi_india_monthly_enriched` | Indian UPI Transactions | 120 | 18 | national-month | 2016-01→2025-12 | 0 |
| `npci_bhim_statistics` | NPCI Products Statistics | 91 | 4 | national-month | 2016-04→2023-10 | 0 |
| `npci_fastag_statistics` | NPCI Products Statistics | 85 | 5 | national-month | 2016-11→2023-11 | 0 |
| `npci_imps_statistics` | NPCI Products Statistics | 122 | 4 | national-month | 2013-09→2023-10 | 0 |
| `npci_star99_statistics` | NPCI Products Statistics | 91 | 4 | national-month | 2016-04→2023-10 | 0 |
| `npci_upi_product_statistics` | NPCI Products Statistics | 92 | 4 | national-month | 2016-04→2023-11 | 0 |
| `npci_upi_remitter_banks` | NPCI Products Statistics | 50 | 8 | bank snapshot | Sep-2023 (single point) | 0 |
| `npci_upi_beneficiary_bank` | NPCI Products Statistics | 50 | 7 | bank snapshot | Sep-2023 (single point) | 0 |
| `npci_upi_payers_performance_psp` | NPCI Products Statistics | 32 | 6 | PSP snapshot | unstated (single point) | 0 |
| `npci_upi_mandate_creation` (mislabeled) | NPCI Products Statistics | 50 | 6 | bank snapshot | unstated (single point) | 0 |
| `npci_year_wise_digital_transaction` | NPCI Products Statistics | 5 | 11 | national-year | FY18-19→FY22-23(partial) | 0 |
| `npci_upi_apps_RAW` | NPCI Products Statistics | 73 | 12 (3-row header) | app snapshot | Oct-2023 (single point) | n/a |

No dataset contains duplicate rows. No dataset showed casing-collapse category issues. The only whitespace/encoding anomalies found are the non-breaking-space digit in `npci_upi_product_statistics` and the cp1252 en-dash in `npci_year_wise_digital_transaction`.

Full per-column detail (nulls, unique counts, percentile summaries, top values) for every dataset above is in `audit_scripts/output/<dataset_name>.json`.
