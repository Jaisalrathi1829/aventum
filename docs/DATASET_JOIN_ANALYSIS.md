# Dataset Compatibility / Join Analysis

Every plausible dataset combination was considered. Only combinations with a real candidate key are tested computationally; combinations with no shared key at all are classified `NOT NEEDED` without a wasted test. All tests are reproducible from `audit_scripts/deep_analysis.py` and `audit_scripts/cross_checks.py`.

---

## 1. `upi_transactions_2024` ↔ `upi_transaction_insights_dataset` (the two transaction-grain files)

**Candidate join keys considered:** `transaction_id`, `date`/`timestamp` (exact and windowed), `amount`, `merchant_category`.

- **`transaction_id` exact match:** `TXN0000000001…` (13-char zero-padded) vs `TXN100000…` (9-char) — **0 exact-string overlaps** out of 250,000 × 500 possible pairs (computationally confirmed, `deep_analysis.py` §F). Different ID generators; not the same transaction population.
- **Date range overlap:** both span 2024-01-01 to 2024-12-30 almost exactly — **but shared date range alone is not a valid join key** for entity-level joins; it only supports independent-population comparison, not row linking.
- **No shared bank, device, or user field exists** to join on (the insights dataset has no bank column at all).
- **Amount as a fuzzy key:** rejected outright — floating-point transaction amounts are not unique/stable identifiers and two independent synthetic populations would produce spurious accidental matches with no semantic meaning.

**Classification: INVALID / FABRICATES RELATIONSHIPS.** These are two independent synthetic populations covering the same calendar year with no reliable linking key. Any row-level join would pair unrelated transactions and fabricate a relationship that does not exist. **Do not join.** They may only be compared in aggregate (e.g., "does the insights dataset's category mix roughly resemble the primary dataset's?") — and even that comparison is weak given the insights dataset's artificial 50/50 success-rate balance (see [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md)).

---

## 2. `upi_transactions_2024` ↔ NPCI bank-level snapshot files (`npci_upi_remitter_banks`, `npci_upi_beneficiary_bank`, `npci_upi_payers_performance_psp`, `npci_upi_mandate_creation`)

**Candidate join key:** bank name (`sender_bank`/`receiver_bank` in the transaction file ↔ bank name column in each NPCI file).

**Key normalization required — tested computationally** (`deep_analysis.py` §E): the transaction file uses short/informal bank names (`SBI`, `HDFC`, `ICICI`, `IndusInd`, `Axis`, `PNB`, `Yes Bank`, `Kotak` — 8 distinct values); the NPCI files use full legal entity names (`State Bank Of India`, `HDFC Bank Ltd`, `ICICI Bank`, `IndusInd Bank`, `Axis Bank Ltd`, `Punjab National Bank`, `Yes Bank Ltd`, `Kotak Mahindra Bank`, plus 50+ other banks not present in the transaction file at all).

- After normalizing case/punctuation/`Ltd`/`Limited` and testing exact + substring matching: **6 of 8** transaction-file banks matched an NPCI name automatically (`IndusInd`→`IndusInd Bank`, `Kotak`→`Kotak Mahindra Bank`, `HDFC`→`HDFC Bank Ltd`, `ICICI`→`ICICI Bank`, `Yes Bank`→`Yes Bank Ltd`, `Axis`→`Axis Bank Ltd`).
- **`SBI` and `PNB` did not match automatically** — they are acronyms with no substring relationship to `State Bank Of India` / `Punjab National Bank`. A join is only possible with a **manually curated bank-alias table**, not a generic string-normalization function.
- **Match/coverage rate:** 8/8 (100%) of transaction-file banks CAN be mapped to an NPCI entity once a manual alias table for the 2 acronyms is added. Conversely, only 8 of the ~50–60 distinct bank names in the NPCI files have any counterpart in the transaction file — the transaction file's 8-bank universe is a small, fixed subset of India's real ~60+-bank UPI ecosystem.
- **Cardinality:** transaction file has many rows per bank (one-to-many from the NPCI bank-attribute row); NPCI files have exactly one row per bank (cross-sectional snapshot, confirmed in [DATASET_GRAIN_ANALYSIS.md](DATASET_GRAIN_ANALYSIS.md)). A join would be **many-to-one**, not many-to-many — safe from row-multiplication risk.
- **Temporal alignment problem:** the NPCI snapshot files are dated Sep-2023 (2 files) or undated (2 files); the transaction file is entirely 2024. **Joining a Sep-2023 cross-sectional BD%/TD% snapshot onto 2024 transactions would silently imply those approval/decline rates still held a year later**, which is not supported by any evidence in this corpus (bank-level approval rates are known to shift month to month, as the NPCI monthly time-series files themselves show for volume/value).

**Classification: POSSIBLE BUT HIGH-RISK.** A many-to-one join is mechanically valid (with a manual 2-entry alias table) and could enrich each 2024 transaction with a *contextual* Sep-2023 BD%/TD% benchmark for its bank — but doing so would implicitly (and wrongly) assert that a static, differently-dated, real-world cross-section describes the behavior of a synthetic 2024 dataset with its own near-uniform failure distribution (per [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md), the transaction file's actual failure-rate spread across banks is only 0.28–0.51 points — far smaller than the real dispersion the NPCI file shows, e.g. Central Bank of India's 53.47% TD%). **Recommended use: read the NPCI BD%/TD% distribution as a *parameter reference* when designing the synthetic incident layer (i.e., "how large can a real bank-level TD% spike plausibly be"), not as a literal per-row join.** See [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md).

---

## 3. `upi_transactions_2024` (or its daily/monthly rollup) ↔ national monthly time-series files (`upi_india_monthly_enriched`, `npci_upi_product_statistics`)

**Candidate join key:** month.

Tested computationally by rolling up `upi_transactions_2024` to monthly transaction counts and comparing against `upi_india_monthly_enriched`'s `Volume_Mn` for the same 2024 months:

| | Avg monthly count |
|---|---|
| `upi_transactions_2024` (this corpus's sample) | ~20,833 transactions/month |
| `upi_india_monthly_enriched` (claimed national volume) | ~14,704,166,667 transactions/month (14,704.17 Mn) |
| **Ratio** | **0.000142%** — the sample represents roughly **1 in 705,800** of the claimed national monthly volume |

**Classification: VALID ONLY FOR AGGREGATION, AS AN ORDER-OF-MAGNITUDE SANITY CHECK — NEVER AS A SCALING OR SHARE-OF-MARKET JOIN.** The month key technically aligns, but `upi_transactions_2024` is not a uniform random sample of the national ecosystem (it has a fixed 8-bank, 10-state, 4-network universe, and near-uniform failure behavior unlike the real ecosystem) — treating its ratio to the national total as a "sampling fraction" and extrapolating (e.g. "if we recovered X% of failures in our sample, that implies ₹Y in national GMV impact") would **fabricate a relationship the data cannot support**. The only legitimate use of this comparison is the one made here: confirming that `upi_transactions_2024` is a small illustrative dataset, not a comprehensive national extract, which shapes how its results must be framed in any demo (see [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md) §I).

The equivalent comparison against `npci_upi_product_statistics` **cannot even be attempted for 2024** — that file's coverage ends 2023-11, one month before the transaction file begins. **NOT NEEDED / impossible: no temporal overlap exists.**

---

## 4. `upi_india_monthly_enriched` ↔ `npci_upi_product_statistics` (both national-monthly UPI series)

Already computed in full in [DATA_PROVENANCE.md](DATA_PROVENANCE.md) §3 (not repeated here per the instruction to perform each analysis once). Summary: 89 overlapping months, 78 (88%) differ by more than a trivial rounding threshold, divergence grows to 840 Mn volume / ₹95,153 Cr value by 2023.

**Classification: INVALID / FABRICATES RELATIONSHIPS if merged as a single reconciled series.** The two files must be kept and cited separately, with `npci_upi_product_statistics` preferred as the more directly-sourced-looking file. See [DATA_PROVENANCE.md](DATA_PROVENANCE.md) for the full reasoning.

---

## 5. `upi_india_monthly_enriched` / `npci_upi_product_statistics` ↔ other NPCI product time-series (BHIM, Fastag, IMPS, `*99#`)

**Candidate join key:** `Month`.

These are **different payment products** (BHIM, FASTag, IMPS, `*99#`, UPI), not different views of the same product — joining them on `Month` is mechanically trivial (all use the same `YY-Mon` key format, 1:1 cardinality, no duplicate months in any file) but produces a **wide multi-product table**, not a validation of any single product's numbers. This was not the intent of any Aventum requirement (UPI is the in-scope product; BHIM/FASTag/IMPS/`*99#` are adjacent NPCI products, useful only as multi-instrument market context, matching `npci_year_wise_digital_transaction`'s role).

**Classification: VALID JOIN (mechanically), NOT NEEDED (for Aventum's UPI-focused scope).** Available if a future "digital payments ecosystem" comparison view is wanted, out of scope for Day 1.

---

## 6. `npci_upi_apps_RAW` ↔ anything else

`npci_upi_apps_RAW` keys on **PSP/app name** (Amazon Pay, BHIM, Axis Bank Apps, etc.), a dimension that does not exist in `upi_transactions_2024` (which has bank, not PSP-app, granularity — a transaction routed via "Axis Bank Apps" vs. a transaction where Axis is simply the `sender_bank` are different concepts, and the transaction file cannot distinguish which UPI app initiated a transaction).

**Classification: NOT NEEDED.** No valid join key exists between app-level and bank-level grain without an unfounded assumption (e.g., "assume every Axis transaction went through Axis Bank Apps," which is false — many UPI transactions are initiated via third-party apps like Google Pay/PhonePe regardless of which bank the account is with, and no such third-party-app field exists in the transaction file to test this).

---

## 7. Geographic join: `upi_transactions_2024.sender_state` ↔ any NPCI file

**No NPCI file in this corpus has a state/region dimension at all** — all 10 NPCI files are either national totals or bank/PSP/app entity totals, none broken out by geography.

**Classification: NOT NEEDED — no key exists on the NPCI side.** Geographic segmentation is only possible within `upi_transactions_2024` alone (see Segmentation Feasibility in [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md)).

---

## Join classification summary

| Combination | Join key | Classification |
|---|---|---|
| `upi_transactions_2024` ↔ `upi_transaction_insights_dataset` | transaction_id / date | **INVALID / FABRICATES RELATIONSHIPS** |
| `upi_transactions_2024` ↔ NPCI bank snapshots (4 files) | bank name (needs manual alias table) | **POSSIBLE BUT HIGH-RISK** — use as parameter reference only, not a row join |
| `upi_transactions_2024` (rollup) ↔ `upi_india_monthly_enriched` | month | **VALID ONLY FOR AGGREGATION** (order-of-magnitude sanity check only) |
| `upi_transactions_2024` (rollup) ↔ `npci_upi_product_statistics` | month | **NOT NEEDED** — no temporal overlap (2024 vs. ≤2023-11) |
| `upi_india_monthly_enriched` ↔ `npci_upi_product_statistics` | month | **INVALID / FABRICATES RELATIONSHIPS** if merged — material numeric contradiction |
| National time-series ↔ other NPCI product series (BHIM/Fastag/IMPS/`*99#`) | month | **VALID JOIN, NOT NEEDED** for UPI-focused scope |
| `npci_upi_apps_RAW` ↔ anything | app/PSP name | **NOT NEEDED** — no shared grain with any other file |
| `upi_transactions_2024.sender_state` ↔ any NPCI file | state | **NOT NEEDED** — no geographic key exists on the NPCI side |

**No merged/joined dataset has been persisted anywhere in this repository.** Every join above was tested in-memory only, inside the audit scripts, and discarded after measurement.
