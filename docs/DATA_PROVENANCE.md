# Data Provenance and Trust

Provenance was assessed strictly from what is present in `data/raw/` — file contents, filenames, folder names, and the single README found. No web lookups were performed. Where evidence is circumstantial rather than confirmed, it is labeled as such.

---

## 1. Folder-by-folder provenance

### `Indian UPI Transactions/` → `upi_india_monthly_enriched.csv`

- **Source:** not stated anywhere in the folder (no README, no citation).
- **Stated description:** none.
- **Stated date range:** the data itself spans 2016-01 to 2025-12; no document states this is the intended range.
- **Collection method:** unknown/unstated.
- **License:** unverifiable — no license file present.
- **Real / synthetic / derived:** the column set (`Volume_MoM_%`, `Volume_YoY_%`, `Month_Sin`, `Month_Cos`, `Volume_RollMean_3M`, `Is_Covid_Period`, `Is_Festive_Season`, `Event_Code`) is self-evidently **engineered/derived**, not a raw export — someone computed rolling statistics, cyclical encodings, and binary event flags before this file was saved. It is therefore, at best, a **derived enrichment of some underlying monthly series**, of unknown fidelity to that underlying series.
- **Confidence this is a faithful representation of real NPCI/UPI ecosystem volume:** **LOW.** See §3 "Contradiction found" below — its numbers do not match the closest comparable raw file in this same corpus for the same months, and it extends 16 months (2023-09 through 2025-12) beyond the coverage of any corroborating raw file we have, with no explanation of how those months were produced (forecast? different source? Not stated).

### `NPCI Products Statistics Since Launch/` (10 files)

- **Source:** not stated in any file — no README, no header citation, no URL. The folder name and column vocabulary (`BD%`/`TD%`/"Banks live on X"/"Deemed Approved%") are consistent with **NPCI's own publicly-published product statistics** (NPCI = National Payments Corporation of India, the actual operator of UPI/IMPS/FASTag/BHIM/*99# in India), which is plausible given these are exactly the products and metrics NPCI publishes. **This plausibility is not independently verified from file contents** — no snapshot date-of-download, no URL, no attribution string appears anywhere in these 10 files.
- **Stated description:** none beyond the filenames themselves.
- **Stated date range:** partially — 2 of the 10 files state a snapshot month in their column header text (`"UPI Beneficiary Banks (Sep-2023)"`, `"UPI Remitter Banks (Sep-2023)"`, `"UPI Apps (Oct'23)"` in a title row); the other 7 state no collection date anywhere.
- **Collection method:** unknown/unstated.
- **License:** unverifiable — no license file present. (If this is genuinely NPCI-published statistical data, NPCI typically publishes such aggregate statistics openly, but that cannot be confirmed from the files themselves, and no license text accompanies them.)
- **Real / synthetic / derived:** presents as **aggregate reporting data** (monthly ecosystem totals, cross-sectional bank/PSP performance snapshots) — plausible as real published statistics given the specificity and internal consistency of the numbers (e.g., a genuine, extreme outlier like Central Bank of India's 53.47% TD% is not the kind of value a naive synthetic generator would typically produce — it reads as a real reported anomaly). **Assessed as: plausibly real, unconfirmed.**
- **Known limitations:** filename/content mismatch on `UPI mandate creation.csv` (documented in [DATASET_INVENTORY.md](DATASET_INVENTORY.md)); 2 of 4 snapshot files carry no date; inconsistent placeholder conventions (`-` vs `NA`).

### `UPI TRANSACTION DATASET/` (README + `.pbix` + images + video, **no CSV**)

- **Source:** explicitly stated — `README.md` names an individual author, **"Saheb Rafique"**, with LinkedIn (`linkedin.com/in/saheb-rafique-87b2b9186`) and GitHub (`github.com/saheb1999`) profile links, and describes this as a personal **Power BI portfolio project** ("UPI Transaction Analysis Dashboard – Power BI Project").
- **Stated description:** "an interactive Power BI dashboard analyzing over **250K+ UPI transactions** totaling **₹328 million** across India," covering time/bank/device/merchant-category/age-group/fraud-trend exploration. Explicitly framed as a **portfolio/demonstration piece** ("ideal for showcasing data storytelling... for stakeholders such as banks, fintech companies, and analysts") and states the dashboard **"simulates"** how a bank/fintech/payment network could track UPI performance — the author's own word is "simulates," not "reports actual operational data."
- **Stated date range:** not explicitly given as a range; KPIs reference "Oct'23"-adjacent peak-hour and fraud figures consistent with the NPCI-adjacent files' time window, but this is not a confirmed date range for the transaction data itself.
- **Collection method:** unstated. No indication the underlying 250K-transaction dataset is a real operational extract versus a generated/simulated one — the author's own "simulates" framing points toward the latter.
- **License:** the README says "Feel free to fork or clone this project for learning, analysis, or presentations" — an informal permissive statement, not a verifiable license (no LICENSE file, no SPDX identifier).
- **Real / synthetic / derived:** **the underlying transaction-level data is not present in this folder at all** — it is embedded inside the binary `UPI_DashBoard.pbix` (9.3 MB, Power BI's proprietary zip-based container), which this audit could not open (no Power BI Desktop or `.pbix`-parsing library available; explicitly out of scope per the task's "no dependencies beyond profiling" boundary). **This folder therefore contributes zero directly-inspectable rows to the corpus.**

---

## 2. Circumstantial cross-dataset link (flagged, not asserted as fact)

The README above describes statistics (250K+ transactions, ₹328M total, 75% Android share, 73.6K peak-hour [4–8PM] transactions, age group 26–35 most active, SBI leading bank, 0.0019% fraud rate) for a dataset that is **not present as a file** in its own folder. Separately, `UPI Transactions 2024 Dataset/upi_transactions_2024.csv` sits in a **differently-named, seemingly unrelated folder** with no cross-reference to the README. Computing the same statistics against that CSV (`audit_scripts/cross_checks.py`, CHECK 2) gives:

| Metric | README claim | Computed from `upi_transactions_2024.csv` | Match? |
|---|---|---|---|
| Row count | "250K+ transactions" | 250,000 | **Exact** |
| Total value | "₹328 million" | ₹327,939,009 | **Match (₹328M rounded)** |
| Android share | "75%" | 75.11% | **Match** |
| Peak-hour (4–8PM) volume | "over 73.6K transactions" | 73,628 | **Match** |
| Most active age group | "26–35" | 26–35 (largest sender-age segment) | **Match** |
| Leading bank | "SBI leads both sending and receiving" | SBI (largest sender_bank and largest receiver_bank) | **Match** |
| Fraud rate | "Only 0.0019%" | 0.1920% (480/250,000) | **Mismatch — off by ~100×** |

**Assessment:** six of seven independent statistics match to a precision that is very unlikely by chance for an unrelated 250,000-row dataset (exact row count, GMV within rounding, exact age/bank leaders, near-exact peak-hour count). The one mismatch (fraud rate) is off by almost exactly two orders of magnitude, which is consistent with a **decimal-placement error while writing the README** (e.g. "0.19%" mistyped as "0.0019%") rather than proof of a different underlying population — but this cannot be confirmed either way without opening the `.pbix`.

**Conclusion — stated explicitly per the task's instruction not to silently reconcile contradictions:** it is **plausible but not confirmed** that `upi_transactions_2024.csv` is identical to, or was generated from the same process/seed as, the dataset visualized in the `UPI_DashBoard.pbix`. This audit does **not** merge, rename, or treat these as the same dataset. The practical implication for Aventum is limited either way: **the images and README in `UPI TRANSACTION DATASET/` may be treated as design/narrative reference for what an incident-style UPI dashboard can show, and nothing more** — they are not additional rows, and no join or combination should be attempted between the README's claims and `upi_transactions_2024.csv` for analytical purposes.

---

## 3. Contradiction found: `upi_india_monthly_enriched` vs `npci_upi_product_statistics`

Both files claim to represent India's monthly UPI transaction volume/value. `audit_scripts/cross_checks.py` (CHECK 1) parsed both files' month keys onto a common calendar and compared overlapping months directly:

- **89 overlapping months** found (2016-04 through 2023-08).
- Early months (2016) match closely (differences ≤ 0.01 Mn volume / ≤ 0.38 Cr value).
- Divergence grows over time: by 2023, differences reach **up to 840.24 Mn volume** and **up to ₹95,153.38 Cr value** in a single month.
- **78 of the 89 overlapping months (88%)** differ by more than a trivial rounding threshold (>0.01 Mn volume).
- `upi_india_monthly_enriched` additionally extends **16 months beyond** `npci_upi_product_statistics`'s coverage (through 2025-12 vs. the NPCI file's max of 2023-11 across the 5 monthly files, 2023-08 for this specific one), with **no corroborating raw source in this corpus** for those extension months.

**This is a genuine, material contradiction between two datasets in the same corpus that purport to measure the same real-world quantity.** Per instructions, it is not silently reconciled here. Two non-exclusive explanations are plausible: (a) `upi_india_monthly_enriched` was built from a different/later NPCI data pull than the static file in this corpus (NPCI restates/revises monthly figures over time, which is a documented real-world practice), or (b) `upi_india_monthly_enriched` includes modeled/estimated/forecast values for at least its later months, consistent with its already-derived (rolling-mean, event-flag) nature noted in §1. **Practical consequence: `upi_india_monthly_enriched` must not be treated as ground truth for the historical NPCI monthly series.** Where a monthly national benchmark is needed, prefer the direct NPCI file (`npci_upi_product_statistics`) and treat `upi_india_monthly_enriched`'s pre-computed features (seasonality encodings, event flags) as optional derived-context signals of unverified accuracy, not as facts.

---

## 4. Overall trust classification per dataset

| Dataset | Real / synthetic / simulated / unclear | Confidence | Basis |
|---|---|---|---|
| `upi_transactions_2024` | **Synthetic (near-certain)** | Medium-high | Perfectly round bank/category/device counts, zero missingness, zero duplicate rows, near-zero failure-rate correlation with any dimension (see [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md)), no PII — combined with the circumstantial link to a README that explicitly says its dashboard "simulates" bank operations. Behaviorally plausible (realistic right-skewed amount distribution, realistic bank market-share ordering) — a *good* synthetic dataset, but a synthetic one. |
| `upi_transaction_insights_dataset` | **Synthetic (near-certain)** | High | Perfect 50/50 class balance on `is_successful`, near-uniform category/location distributions, amount capped just under a round number — classic ML-tutorial-style generation. |
| `upi_india_monthly_enriched` | **Derived from an unverified/uncorroborated base, possibly partly modeled** | Low | Already-engineered features present in the raw file; material numeric contradiction with the one corroborating raw source in this corpus (§3); extends past that source's coverage with no stated method. |
| NPCI monthly time-series (5 files) | **Plausibly real published statistics** | Medium | Internally consistent, growth patterns match well-known UPI adoption trajectory (near-zero in 2016, exponential growth through 2020-23), no engineered-feature columns, filename/metric vocabulary matches NPCI's known reporting categories. Not independently confirmed (no citation in-file). |
| NPCI single-snapshot bank/PSP files (4 files) | **Plausibly real published statistics** | Medium | Same reasoning; the Central Bank of India 53.47% TD% outlier (§ Data Quality Report) is the kind of specific, non-round anomaly consistent with real reported data rather than a synthetic generator. |
| `npci_upi_apps_RAW` | **Plausibly real published statistics** | Medium | Same reasoning; large PSP-name roster (Amazon Pay, Airtel Payments Bank, BHIM, etc.) with heterogeneous, non-round values across 70 apps. |
| `npci_year_wise_digital_transaction` | **Plausibly real published statistics** | Medium | Coarse but consistent with known multi-instrument volume ordering (UPI overtaking Cards by FY20-21) in the real Indian digital-payments market. |
| `UPI_DashBoard.pbix` (unopened) | **Unclear — not inspectable** | N/A | Contents not accessible to this audit; see §1. |

**No dataset in this corpus is confirmed as unambiguously real, operational, production payment-gateway data.** The strongest-trust items are the NPCI-style aggregate files (plausibly real published statistics, but uncited), and the weakest-trust item still in analytical use is `upi_india_monthly_enriched` (contradicted by the corpus's own NPCI file). The two transaction-level files should be **treated and disclosed as synthetic** in any Aventum demo or documentation — see the explicit disclosure requirement in [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md) §H "data assumptions that must be stated to judges."

---

## 5. Provenance / role assignment (Section 17 of the audit brief)

| Dataset | Eventual role | Why |
|---|---|---|
| `upi_transactions_2024` | **PRIMARY TRANSACTION DATA** | Only dataset with production-scale transaction grain, full-year second-level timestamps, a real success/failure field, and enough dimensional richness (bank, device, network, geography, category) to anchor the canonical schema. |
| `upi_transaction_insights_dataset` | **SUPPORTING DATA (limited)** | Alternative transaction-level vocabulary (`payment_mode`, `location_type`) worth referencing when designing canonical fields, but too small (500 rows) and too artificially balanced to use as an analytical base. |
| `upi_india_monthly_enriched` | **REFERENCE / BENCHMARK DATA (low-confidence)** | Usable only for rough national-trend sanity-checking, and only with the caveat in §3 attached every time it is cited. |
| NPCI monthly time-series (5 files) | **REFERENCE / BENCHMARK DATA** | National-scale sanity-check for whether a demo's implied volumes are the right order of magnitude; also the preferred source over `upi_india_monthly_enriched` when the two conflict. |
| NPCI single-snapshot bank/PSP files (4 files) | **REFERENCE / BENCHMARK DATA** + **SYNTHETIC BASELINE INPUT** | Cross-sectional benchmark of realistic inter-bank BD%/TD% variance; directly informs realistic parameter ranges when synthesizing an incident (e.g., how large a real degradation can plausibly get — see the Central Bank of India outlier). |
| `npci_upi_apps_RAW` | **REFERENCE / BENCHMARK DATA** | App/PSP-level context only; not used in the core pipeline. |
| `npci_year_wise_digital_transaction` | **REFERENCE / BENCHMARK DATA** | Coarse multi-year, multi-instrument context only. |
| `UPI TRANSACTION DATASET/` (README, images, `.pbix`, video) | **DO NOT USE (as data)** | Contains no directly-inspectable rows; README/images may inform demo narrative/UI design only, never analytical claims. |

Datasets are **not** forced into one unified table. `upi_transactions_2024` stands alone as the transaction fact table; the NPCI files and `upi_india_monthly_enriched` are queried and cited separately as context/benchmarks, never physically merged into the transaction table (see [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) for why row-level joins between them are invalid or unnecessary).
