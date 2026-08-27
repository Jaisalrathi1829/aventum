# Dataset Grain Analysis

For every dataset: what does one row actually represent? Determined from repeated-value structure and candidate-key uniqueness measured in [DATASET_INVENTORY.md](DATASET_INVENTORY.md), not assumed from column names.

---

## `upi_transactions_2024`

- **Row grain:** one payment transaction. `transaction id` is 100% unique across 250,000 rows (confirmed PK); `timestamp` is 99.44% unique (collisions are multiple distinct transactions landing in the same second — expected at this volume, not a grain violation).
- **Entity grain:** individual transaction between a sender and receiver bank; no persistent user/account identifier exists (no `sender_id`/`user_id` column), so this is *transaction*-grain, not *customer*-grain — repeat-customer behavior cannot be tracked.
- **Temporal grain:** second-level timestamp.
- **Geographic grain:** `sender_state` only (10 Indian states present, no receiver-side geography, no city/pincode).
- **Additivity:** `amount (INR)` is additive (summable into GMV). `transaction_status`, `fraud_flag`, `is_weekend` are booleans/categoricals, additive only as counts. `hour_of_day`/`day_of_week` are derived from `timestamp`, not independently additive.
- **Row overlap:** none — each row is a distinct transaction (0 duplicate rows, 0 duplicate PKs).
- **Suitability:** **event-level analysis: YES.** This is the only dataset in the corpus with true transaction grain at production scale (250K rows, full calendar year, second-level time). This is the backbone candidate for Aventum's transaction-event table.

## `upi_transaction_insights_dataset`

- **Row grain:** one payment transaction. `transaction_id` 100% unique across 500 rows.
- **Entity grain:** transaction only — no bank, no user identifier, no geography beyond a coarse `location_type` (Urban/Rural/Semi-Urban), i.e. area-type, not state/city.
- **Temporal grain:** `date` (day) + separate `time` (`HH:MM`, minute) string — **not merged into one timestamp column**, so true datetime grain requires a transformation, and even after merging there is no seconds component.
- **Additivity:** `amount` additive.
- **Row overlap:** none.
- **Suitability:** event-level in principle, but at n=500 over a 365-day span (~1.4 rows/day) the *volume* is far too sparse to support any per-segment or per-time-window analysis — see analytical-sufficiency discussion in [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md#8-analytical-sufficiency). Usable only as a small illustrative/reference sample, not as an analytical base.

## `upi_india_monthly_enriched`

- **Row grain:** one calendar month, **national ecosystem aggregate** (all of India, all banks, all UPI apps combined into a single number per month). `Date` is 100% unique (120 distinct months, 2016-01 → 2025-12).
- **Entity grain:** none — there is no bank, app, or region dimension; the whole country is one row per month.
- **Temporal grain:** month. There is no way to recover daily or intra-day behavior from this file — it is a pre-aggregated summary, not a rollup you can drill into.
- **Additivity:** `Volume_Mn`/`Value_Cr` are additive across months (they are monthly totals, confirmed non-cumulative by the near-linear month-over-month growth pattern with no resets). **`Volume_MoM_%`, `Value_MoM_%`, `Volume_YoY_%`, `Value_YoY_%`, `Month_Sin`, `Month_Cos`, `Volume_RollMean_3M`, `Value_RollMean_3M`, `Is_Covid_Period`, `Is_Festive_Season`, `Event_Code` are NOT additive** — they are already-computed analytical features (rolling statistics, cyclical encodings, period/event flags) baked into the raw file itself. This file therefore mixes "observed-ish" facts with pre-derived analytics in the same row — a modeling hazard flagged again in [DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md).
- **Row overlap:** none (one row per distinct month).
- **Suitability:** **event-level analysis: NO.** Suitable only for high-level national trend/benchmarking context (e.g. "is Aug-2023 volume in the same order of magnitude as the ecosystem?"). Not usable for anomaly detection at anything finer than monthly national resolution, and — per [DATA_PROVENANCE.md](DATA_PROVENANCE.md) — its own numbers materially diverge from the NPCI product-statistics file for the same months, so even at monthly grain it cannot be treated as ground truth without caveats.

## NPCI monthly time-series files (`npci_bhim_statistics`, `npci_fastag_statistics`, `npci_imps_statistics`, `npci_star99_statistics`, `npci_upi_product_statistics`)

- **Row grain:** one calendar month, **national ecosystem aggregate for one payment product** (BHIM, FASTag, IMPS, *99#, or UPI respectively). `Month` is 100% unique within each file.
- **Entity grain:** none (whole-country totals per product).
- **Temporal grain:** month.
- **Additivity:** Volume/Value columns are additive monthly totals (comma-formatted, requires numeric cleaning first — see [DATASET_INVENTORY.md](DATASET_INVENTORY.md)). "No. of Banks live" is a **stock**, not a flow — it is not meaningful to sum across months (it should only be read as a point-in-time count, e.g. "216 banks were live on BHIM as of Dec-2021").
- **Row overlap:** none within each file; the 5 files overlap each other only in the `Month` key (different products, same calendar).
- **Suitability:** aggregate benchmarking only, same limitations as `upi_india_monthly_enriched` above, one level more granular only in the sense of being product-specific rather than blended.

## NPCI single-snapshot entity files (`npci_upi_remitter_banks`, `npci_upi_beneficiary_bank`, `npci_upi_payers_performance_psp`, `npci_upi_mandate_creation`)

- **Row grain:** one **bank** (Remitter/Beneficiary/mandate-creation files) or one **PSP** (Payers Performance file), for a **single unstated-or-partially-stated point in time** — this is a **cross-sectional snapshot**, not a time series. Confirmed: each file has exactly one row per distinct bank name (Sr.No + bank name both 100% unique) and there is no date/period column repeating per bank — the period, where present at all, is only in the file's column header text (`"UPI Beneficiary Banks (Sep-2023)"`), not in the data itself.
- **Entity grain:** bank / PSP.
- **Temporal grain:** **none at the row level** — one moment in time for the whole file. `npci_upi_payers_performance_psp` and `npci_upi_mandate_creation` do not even state which moment.
- **Additivity:** `Total Volume` is additive across banks (sums to an ecosystem total); `Approved%`, `BD%`, `TD%`, `Debit Reversal Success%` are **rates, not additive** — they must be recombined via a weighted average using volume, never summed directly.
- **Row overlap:** none (unique bank per row).
- **Suitability:** **NOT suitable for event-level or temporal-onset analysis** — there is exactly one observation per bank, so no "before/after" or trend can be derived from within these files alone. Suitable only as a **cross-sectional benchmark** of realistic inter-bank variance in approval/decline behavior at one moment (see [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md) for how `BD%`/`TD%` inform a root-cause taxonomy).

## `npci_year_wise_digital_transaction`

- **Row grain:** one **financial year**, national ecosystem, spanning multiple payment instruments (Cards/UPI/IMPS/RTGS/NEFT) as parallel columns rather than a stacked/long format.
- **Entity grain:** none (nation-wide).
- **Temporal grain:** year (5 rows: FY18-19 through FY22-23, the last partial — "till Jan").
- **Additivity:** each instrument's Volume/Value is additive across years; the columns are not additive against each other (Cards vs UPI vs IMPS are different instruments, summing them would conflate unlike measures unless deliberately building a total-digital-payments metric).
- **Suitability:** only for coarse multi-year, multi-instrument context ("how big is UPI relative to Cards/NEFT/RTGS nationally"). Too coarse (5 data points) for any trend modeling.

## `npci_upi_apps_RAW`

- **Row grain:** one **PSP application** (e.g. "Amazon Pay", "BHIM", "Axis Bank Apps"), for the single snapshot **Oct-2023** stated in the title row.
- **Entity grain:** app/PSP.
- **Temporal grain:** none at row level (one snapshot month for the whole file, per the 3-row multi-index header — see [DATASET_INVENTORY.md](DATASET_INVENTORY.md)).
- **Additivity:** Volume(Mn)/Value(Cr) figures are additive across apps within the same transaction-category column; the 5 category groups (Customer Initiated, B2C, B2B, On-us, Total) are **not mutually exclusive versus "Total"** — "Total" is explicitly the sum of the other 4, so including both "Total" and the 4 sub-categories in an aggregation would double-count.
- **Suitability:** app-level cross-sectional benchmark only, same limitation as the bank-snapshot files above.

---

## Grain summary table

| Dataset | Row = | Entity grain | Temporal grain | Additive core measure? | Event-level suitable? |
|---|---|---|---|---|---|
| `upi_transactions_2024` | 1 transaction | transaction | second | Yes (amount) | **YES** |
| `upi_transaction_insights_dataset` | 1 transaction | transaction | day+minute (separate) | Yes (amount) | Only as tiny illustrative sample |
| `upi_india_monthly_enriched` | 1 month, national | none | month | Yes (Volume/Value only) | No — aggregate only |
| `npci_bhim/fastag/imps/star99/upi_product_statistics` | 1 month, national, per product | none | month | Yes (Volume/Value); "Banks live" is a stock | No — aggregate only |
| `npci_upi_remitter_banks` / `npci_upi_beneficiary_bank` | 1 bank, one snapshot | bank | none (cross-section) | Volume yes, rates no | No — cross-section only |
| `npci_upi_payers_performance_psp` | 1 PSP, one snapshot (undated) | PSP | none (cross-section) | Volume yes, rates no | No — cross-section only |
| `npci_upi_mandate_creation` (mislabeled) | 1 bank, one snapshot (undated) | bank | none (cross-section) | Volume yes, rates no | No — cross-section only |
| `npci_year_wise_digital_transaction` | 1 financial year, national | none | year | Yes, per instrument | No — coarse aggregate only |
| `npci_upi_apps_RAW` | 1 app, one snapshot (Oct-2023) | app/PSP | none (cross-section) | Yes, within category (not vs. Total) | No — cross-section only |

**Conclusion:** exactly **two** of the fourteen tabular datasets are event/transaction grain (`upi_transactions_2024`, `upi_transaction_insights_dataset`); everything else is a pre-aggregated national/entity summary at month, year, or single-snapshot grain. Aventum's core "detect a sudden deterioration" capability therefore has only one dataset with the volume and time resolution to plausibly support it: `upi_transactions_2024`.
