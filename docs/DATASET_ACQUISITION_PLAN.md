# Dataset Acquisition Gap Analysis

Determines whether Day 2 should download any additional dataset before implementation begins, based strictly on the gaps already measured in [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) and [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md). No web search was performed to fill any gap — per the task's explicit boundary, this document only reasons about *whether* a download would help and what it would need to contain, not which specific URL to fetch.

---

## A. Unresolved requirements (materially affecting demo credibility)

Pulled from the classifications in [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) that are `NOT CREDIBLY SUPPORTED`, `SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT`, or a `WEAK PROXY`, filtered to only those that materially matter (trivial gaps omitted):

1. Gateway identity / routing path / routing policy — no field, no proxy.
2. Gateway latency (processing time) — no field, no proxy.
3. Gateway response/health state — no field, no proxy.
4. Granular per-transaction failure/error reason code — no field; only an aggregate 2-category (BD/TD) proportion proxy exists.
5. Time-bounded, labeled incident ground truth (onset, resolution, root cause) — no field, no proxy beyond one uncontextualized cross-sectional outlier.
6. Post-action / live-continuation data for Verification — structurally absent from a static historical CSV.
7. Bank coverage limited to 8 of ~50–60 real UPI-participating banks.
8. Geographic coverage limited to 10 of 28+ states/UTs, sender-side only.
9. National monthly UPI benchmark for the transaction file's own year (2024) — `npci_upi_product_statistics` only covers through 2023-11, and the one 2024-covering file (`upi_india_monthly_enriched`) is contradicted by that same NPCI file for overlapping months ([DATA_PROVENANCE.md](DATA_PROVENANCE.md) §3).

## B. Can another dataset realistically solve each gap?

| # | Gap | Solvable by an existing `data/raw/` file? | Realistically solvable by another public dataset? | Must it be transaction-level? | Min temporal resolution needed | Min sample/coverage needed | Geographic scope needed | Join key needed | Legitimately joinable to current data? | Would downloading it actually help? |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 Gateway/routing | No | **No** — real-time gateway/routing decisions are internal payment-processor telemetry, not published as open data by any payment network for competitive/security reasons | N/A | N/A | N/A | N/A | none | Would help only if paired 1:1 with our exact transaction rows, which no external dataset can be | **No** |
| 2 Gateway latency | No | **No** — same reasoning; processing-time telemetry is not publicly released at transaction grain by any real gateway operator | N/A | N/A | N/A | N/A | none | Same | **No** |
| 3 Gateway health/response | No | **No** — same reasoning | N/A | N/A | N/A | N/A | none | Same | **No** |
| 4 Granular error/reason codes | No (only the BD/TD aggregate proxy) | **Possibly, as a code-vocabulary reference only** — NPCI/UPI publish standardized response-code documentation in some public forms (a code → meaning lookup table), which is a plausible candidate IF a legitimate, verifiable source is located | No — a code dictionary is reference data, not transaction data | monthly/static is fine | a handful of rows (one per code) | national | none (used only as a vocabulary lookup, not joined) | Not joined at all — used only to inform the *vocabulary* of synthetic codes | **Marginally** — improves realism of labels shown in the UI, does not change any analytical result |
| 5 Incident ground truth | No | **No** — an incident label is inherently paired to *our specific* transaction rows; no external dataset can supply ground truth for a dataset it was never generated against | N/A | N/A | N/A | N/A | none | Not joinable by construction | **No** |
| 6 Live/post-action continuation | No | **No** — this is a live-system/architecture requirement (a running feed), not a static file to download | N/A (would need to be a stream, not a dataset) | N/A | N/A | N/A | none | N/A | **No** — solved by simulation-of-continuation, not acquisition |
| 7 Broader bank coverage | No | **Unlikely to help** — any other synthetic UPI transaction dataset found publicly would be an *independent* population with no reliable join key to `upi_transactions_2024`, exactly as `upi_transaction_insights_dataset` was shown to be ([DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §1). Adding it would not extend the *same* transactions' bank coverage — it would just be a second, disconnected sample. | would need to be transaction-level | daily-or-finer | 100K+ rows to be worth the integration cost | national, 50+ banks | transaction_id / timestamp (neither reliable across independent synthetic sources, per prior finding) | **No, not without fabricating a relationship** | **No** |
| 8 Broader geographic coverage | No | Same reasoning as #7 | same | same | same | 28+ states/UTs | same | **No** | **No** |
| 9 2024 national monthly UPI benchmark | Partially (`npci_upi_product_statistics` exists but stops 2023-11) | **Yes, plausibly** — NPCI publishes updated monthly product statistics over time; a more recent pull covering through Dec-2024 is a realistic, low-risk acquisition target | No — this is aggregate reference data, matching the grain already used for `npci_upi_product_statistics` | monthly | 12 rows (Jan–Dec 2024) | national | Month | **Yes — same key/grain as the existing NPCI file, used only as a separate reference table, never merged into `transactions`** | **Yes** — closes the one real, addressable evidentiary gap: we currently have no independently-sourced national benchmark for the exact year our primary transaction file claims to represent |

## C. DOWNLOAD vs SYNTHESIZE vs DERIVE vs DROP

| Gap | Decision | Reasoning |
|---|---|---|
| 1 Gateway/routing/policy | **SYNTHESIZE** | No public dataset can legitimately supply this; attempting one would either be unfindable or would require fabricating a relationship. Matches the project brief's own plan for a synthetic infrastructure layer. |
| 2 Gateway latency | **SYNTHESIZE** | Same reasoning. |
| 3 Gateway health/response | **SYNTHESIZE** | Same reasoning. |
| 4 Granular error/reason codes | **SYNTHESIZE** (proportions calibrated from the existing NPCI BD%/TD% proxy); **DOWNLOAD IF a high-quality public NPCI/UPI response-code reference is found** to enrich the *vocabulary* only | Per-transaction labels must be synthetic regardless; a public code dictionary (if one exists and is verifiable) would only improve label realism, not analytical validity — worth a lightweight search, not a blocking requirement. |
| 5 Incident ground truth | **SYNTHESIZE** | Ground truth must be paired to our own data by construction; no external dataset can supply this. |
| 6 Live/post-action continuation | **SYNTHESIZE** (as a simulated continuation of the incident window) | Not a dataset-acquisition problem at all — an architecture/simulation decision for Day 2+. |
| 7 Broader bank coverage | **DROP** | Per [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md), any candidate replacement/supplement dataset would be an unjoinable independent population; the existing 8-bank universe is already sufficient for the segmentation feasibility Aventum needs (§10 of the Requirements Matrix), and the honest scoping condition in [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md) already addresses this limitation via disclosure rather than acquisition. |
| 8 Broader geographic coverage | **DROP** | Same reasoning as #7 — not required for MVP credibility, disclosure is sufficient. |
| 9 2024 national monthly UPI benchmark | **DOWNLOAD** | The one gap in this list with a realistic, low-risk, non-joined public-data solution that materially improves an existing weak point (the `upi_india_monthly_enriched` contradiction). |

## D. Ideal additional dataset specification (for the one DOWNLOAD-classified gap)

**Gap #9 — updated NPCI monthly UPI product statistics through 2024:**

- **Ideal dataset type:** national monthly aggregate statistics for UPI (and ideally BHIM/FASTag/IMPS/`*99#` for consistency with the existing 5-file set), same shape as `npci_upi_product_statistics`.
- **Ideal source characteristics:** an official or clearly-sourced NPCI statistics export, ideally with an explicit publish/download date and citation — an improvement over the current file's total lack of citation.
- **Required columns:** `Month`, `Volume (in Mn)`, `Value (in Cr.)`, and ideally `No. of Banks live` for consistency with the existing file's schema.
- **Minimum row count:** 12 (Jan–Dec 2024), ideally extending further back to overlap and cross-validate against the existing `npci_upi_product_statistics` file's 2016–2023 coverage.
- **Minimum time resolution:** monthly (matches the existing file; no finer resolution is realistically published at the national aggregate level).
- **Required failure information:** none needed at this grain — this file's role is volume/value benchmarking only, not failure analysis.
- **Required banking/payment-method information:** not required for this specific gap.
- **Required infrastructure information:** none — explicitly out of scope for this download (infrastructure fields are SYNTHESIZE-classified above).
- **Required identifier/join key:** `Month`, in a format normalizable to the existing `YY-Mon` or ISO convention (already handled by the audit's `parse_npci_month` logic).
- **Acceptable alternatives:** any other clearly-sourced, dated national UPI monthly aggregate (volume/value) publication, even from a secondary aggregator, provided it states its own source and date.
- **Unacceptable substitutes:** another engineered/enriched file with pre-computed rolling means, seasonality encodings, or event flags (i.e., another `upi_india_monthly_enriched`-shaped file) — the whole point of this acquisition is to obtain a **more directly-sourced** series, not another derived one with the same trust problem.

## E. Priority ranking

| Gap | Priority |
|---|---|
| Gap #9 (2024 national monthly UPI benchmark) | **P1 — materially improves Aventum** (strengthens a documented weak point, low integration risk) |
| Gap #4's optional code-vocabulary reference | **P2 — optional enhancement** (cosmetic/realism improvement only) |
| Gaps #1, #2, #3, #5, #6 (gateway/routing/latency/health, incident ground truth, live continuation) | **DO NOT DOWNLOAD** — correctly addressed by SYNTHESIZE, not acquisition |
| Gaps #7, #8 (broader bank/geographic coverage) | **DO NOT DOWNLOAD** — correctly addressed by DROP/disclosure |

## F. Redundancy check

Before recommending Gap #9's download, confirmed it is **not** already available through derivation, aggregation, proxy, or combination of current files: `npci_upi_product_statistics` stops at 2023-11 (11 months short of even beginning 2024); `upi_india_monthly_enriched` claims 2024 coverage but is independently shown to materially contradict the one corroborating file we have ([DATA_PROVENANCE.md](DATA_PROVENANCE.md) §3), so it cannot substitute for a trustworthy 2024 benchmark. No other file in the corpus contains national monthly UPI volume/value at all. **The gap is real and not already covered.**

## G. Final acquisition decision

### Additional datasets we SHOULD download

- **Updated NPCI monthly UPI product statistics extending through 2024** — closes Gap #9. Solves: "we currently have no trustworthy national monthly benchmark for the exact year our primary transaction dataset represents." Required fields: `Month`, `Volume (in Mn)`, `Value (in Cr.)`. Integration method: loaded as a **separate reference table** (`npci_reference_benchmarks`-adjacent, or its own small reference table), cited alongside `upi_transactions_2024` for context only — **never joined row-level**, consistent with every join-risk finding in [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md).

### Additional datasets we SHOULD NOT download

- Any additional UPI **transaction-level** dataset (to broaden bank/geography coverage or increase volume) — no reliable join key would exist to the current primary dataset (demonstrated computationally for the one such dataset we already have, [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §1), so it would sit as a second disconnected population rather than enriching the existing one.
- Any dataset claiming to provide "real gateway/routing/latency logs" — not realistically publicly available for a real payment network, and any dataset claiming to be this should be treated with heavy skepticism rather than acquired.
- Any dataset claiming to provide real "UPI mandate" statistics to replace the mislabeled file — not a blocker for the MVP (mandates are out of Aventum's core scope), so not worth acquisition effort.

### Fields that MUST be synthetically generated

Gateway identity, routing path, routing policy, gateway latency, gateway response/health state, per-transaction error/reason codes, and all incident-metadata fields (`incident_id` through `ground_truth_root_cause`) — none of these are realistically obtainable from any public dataset, for the reasons in §B/§C above. This matches the SYNTHESIZE decisions already reflected in [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md).

### Fields already sufficient in current data

Transaction identity, timestamp, amount, status, payment method, merchant category (with the P2P-null caveat), sender-side region, device, network, sender/receiver bank (8-bank universe), and fraud flag — all directly usable from `upi_transactions_2024` as established in [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §A.

### Final recommendation

# DOWNLOAD ONE OR MORE DATASETS ONLY IF A HIGH-QUALITY SOURCE IS FOUND

Specifically: search for an updated NPCI monthly UPI statistics export covering 2024 (Gap #9, P1) before Day 2 modeling work that depends on a national benchmark; treat it as valuable-but-not-blocking (the core Aventum pipeline does not depend on it — it only strengthens contextual/benchmarking claims). Do not spend acquisition effort on any transaction-level, gateway/routing, or incident-ground-truth dataset — those are correctly and only solved by the synthetic infrastructure/incident layer already planned in the project brief.
