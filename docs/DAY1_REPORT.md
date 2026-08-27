# Day 1 Report — Master Dataset Audit, Data Feasibility, Canonical Data Design

This report rolls up the full Day 1 audit. Every claim below is backed by a computation in `audit_scripts/` and a detailed writeup in the linked document — nothing here is re-derived, only summarized.

---

## Executive Summary

`data/raw/` contains 22 files across 4 folders: 14 profilable tabular datasets and 8 non-tabular files (1 README, 1 Power BI binary, 4 images, 1 video, plus the README already counted). Exactly **one dataset — `upi_transactions_2024` (250,000 rows, 17 columns, full 2024 calendar year, second-level timestamps)** — is transaction-grain at production scale and is the only credible backbone for Aventum's core pipeline. It is assessed as **synthetic** (near-certain), with a strong but unconfirmed circumstantial link to an unrelated folder's Power BI dashboard README. Ten NPCI-branded aggregate-statistics files are plausibly real published data (medium confidence, uncited) but are monthly-or-coarser, national-or-bank-cross-sectional grain — useful only as reference/benchmark context, never as a transaction-level source. One file (`upi_india_monthly_enriched`) materially contradicts the corpus's own NPCI benchmark for the same months and must not be treated as ground truth. One file (`UPI mandate creation.csv`) is mislabeled and must never be cited as mandate data. **Aventum's entire infrastructure dimension (gateway, routing, latency, health, error code) and all incident ground truth have zero support anywhere in this corpus and must be built by the project's own synthetic layer** — this was sized precisely, not assumed. Overall readiness: **4.7/10**. Final decision: **YES, WITH EXPLICIT CONDITIONS** — see [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md).

## Raw Dataset Inventory

22 files: 12 CSVs (11 genuine + 1 `.xlsx.csv` that is plain CSV), 1 README, 1 `.pbix`, 4 images, 1 video. No Excel/JSON/Parquet/SQL files exist despite being checked for. Full profile of every file, including nulls, uniqueness, encoding, and malformed values: [DATASET_INVENTORY.md](DATASET_INVENTORY.md).

## Dataset Grain

Only `upi_transactions_2024` and `upi_transaction_insights_dataset` are transaction-grain; the other 12 tabular datasets are monthly-national, yearly-national, or single-snapshot-entity aggregates. Full grain determination per dataset, including additivity analysis: [DATASET_GRAIN_ANALYSIS.md](DATASET_GRAIN_ANALYSIS.md).

## Data Quality

`upi_transactions_2024`: 0 nulls, 0 duplicates, realistic 95.05% success rate, but failure-rate variance across every dimension is only 0.2–0.9 points (noise-level). `upi_transaction_insights_dataset`: artificially balanced 50/50 success label. NPCI files: comma/percent-formatted text numerics, inconsistent placeholder conventions, one non-breaking-space malformed value, one non-UTF-8 file. Full detail: [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md).

## Provenance

No dataset is confirmed as unambiguously real operational data. `upi_transactions_2024`/`upi_transaction_insights_dataset` are assessed synthetic; NPCI files are plausibly real but uncited; `upi_india_monthly_enriched` is low-confidence and contradicted. A circumstantial (unconfirmed) link between `upi_transactions_2024` and the `UPI TRANSACTION DATASET/README.md` Power BI project is documented with 6-of-7 matching statistics. Full detail and role assignment: [DATA_PROVENANCE.md](DATA_PROVENANCE.md).

## Dataset Compatibility / Actual Join Results

Every plausible combination was tested. Only one join (transaction↔bank, via a manual 2-entry acronym alias table) is even mechanically viable, and it is classified HIGH-RISK for row-level use due to temporal mismatch (Sep-2023 snapshot vs. 2024 transactions). The two transaction-grain files have zero ID overlap and no reliable key — INVALID to join. `upi_india_monthly_enriched` vs. `npci_upi_product_statistics` is INVALID to merge (material contradiction). Full computational results: [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md).

## Field Mapping / Valid Proxies

5 EXACT fields, 4 STRONG proxies, 2 WEAK proxies, and 6 fields with **no proxy at all** (the entire gateway/routing/latency/health/incident-metadata group). Full classification: [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md).

## Aventum Requirements Coverage

Every capability in Payment Monitoring, Anomaly Detection, Root-Cause Analysis, Explanation, Counterfactual Simulation, Recovery Recommendation, and Verification was classified 1–5. Payment Monitoring is mostly DIRECTLY SUPPORTED/DERIVED; Anomaly Detection and Root-Cause Analysis have working mechanisms but weak native signal (near-uniform failure rates); Counterfactual Simulation and most of Recovery Recommendation/Verification are SYNTHETIC-ENRICHMENT-gated. Full matrix: [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md).

## Temporal Feasibility

Native transaction density supports **daily** anomaly detection reliably (~687 tx/day, ~30% natural band); **hourly** is borderline (~28.5/hr); **10-minute windows are not statistically usable natively** (12.46% empty, 51.7% under 5 transactions). A genuine 10-minute incident demo requires synthetic volume densification within the injected window. Full analysis: [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §9.

## Segmentation Feasibility

Every tested 2-way segment intersection (bank×device, bank×state, bank×category, device×network, bank×network, state×category, bank×transaction-type) is statistically usable at full-year grain (0 sparse cells even at n<100) — but this collapses inside narrow incident time windows, since the same cells average as few as ~2.8 rows/day. Full analysis: [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §10.

## Counterfactual Feasibility

Replay and attribute-preservation are fully supported by real data. Altering routing, comparing gateway policies, and estimating recovery/GMV impact are entirely synthetic-model outputs, since no routing/gateway dimension exists in any dataset to perturb. All 9 of the brief's counterfactual questions answered explicitly: [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §11.

## Ground-Truth Feasibility

No dataset marks any real incident. The single relevant real-world evidence point (Central Bank of India, 53.47% TD% in a Sep-2023 NPCI snapshot) is an existence proof only, with no time-boundedness or causal narrative. Ground truth must be entirely introduced by Aventum's own synthetic incident-injection layer, and must never be fed into the diagnosis pipeline as input (evaluation-only). Full analysis: [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §12.

## Leakage Risks

Overall risk is low. One medium caveat (`fraud_flag` timing — treat as post-hoc, not live-scoring input) and one medium-high conditional risk (`upi_india_monthly_enriched`'s rolling-mean columns include their own current-period value — never reuse as a same-row baseline). Full detail: [DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md).

## Canonical Schema

12 observed fields, 8 derived fields, 6 synthetic fields, 6 incident-ground-truth fields, organized into Transaction / Payment Context / Banking-Issuer / Infrastructure / Derived Analytics / Incident Metadata. Full schema with per-field source and transformation: [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md).

## Database Design

13 proposed tables (`transactions`, `banks`, `npci_reference_benchmarks`, `gateways`, `gateway_metrics`, `routing_policies`, `incidents`, `incident_evidence`, `simulations`, `simulation_results`, `recommendations`, `actions`, `verification_results`, `audit_events`) with full column/constraint/relationship specification. No database initialized. Full design: [DATABASE_DESIGN.md](DATABASE_DESIGN.md).

## Data Readiness Score

**Overall Aventum Data Readiness: 4.7/10**, itemized across 20 dimensions — strong (6–9) on transaction richness/volume/amount/method/device-network, near-zero (1–2) on gateway/latency/routing/failure-reason visibility. Full itemization: [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md) §18.

## Feasibility Decision

# YES, WITH EXPLICIT CONDITIONS

Full A–J reasoning, required disclosures, and forbidden claims: [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md) §19.

## Recommended Day 2 Plan

1. Implement the canonical `transactions` table and load `upi_transactions_2024` with the documented cleaning rules (merchant_category nulled for P2P, bank-name aliasing table applied where NPCI cross-referencing is needed).
2. Build the synthetic infrastructure layer (`gateways`, `gateway_metrics`, `routing_policies`) with parameters calibrated against the NPCI BD%/TD% proportions — disclosed as synthetic throughout.
3. Build the synthetic incident-injection layer (`incidents`, `incident_evidence`) capable of producing a genuinely concentrated, time-bounded failure spike — sized to be detectable at hourly-or-coarser native resolution, or paired with synthetic volume densification if a sub-hour demo is required.
4. Implement the deterministic derived-analytics layer (rolling success rate, GMV, error rate, anomaly score) as strictly trailing, current-value-excluding computations — explicitly not modeled after `upi_india_monthly_enriched`'s leakage-prone rolling columns.
5. Optionally acquire an updated NPCI monthly UPI statistics export through 2024 (P1, non-blocking) per [DATASET_ACQUISITION_PLAN.md](DATASET_ACQUISITION_PLAN.md) — load as a separate, never-joined reference table.
6. Carry every disclosure condition from [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md) §19 (H/I) into whatever demo narrative or documentation Day 2+ produces.

---

## Decision Table

| Question | Answer | Evidence |
|---|---|---|
| Best primary dataset | `upi_transactions_2024` | Only production-scale transaction-grain dataset; 250,000 rows, 0 nulls, 0 duplicates ([DATASET_INVENTORY.md](DATASET_INVENTORY.md)) |
| Best supporting dataset | `npci_upi_remitter_banks` / `npci_upi_beneficiary_bank` / `npci_upi_payers_performance_psp` | Real, dated BD%/TD% cross-section — best available calibration reference for synthetic error-code proportions ([FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)) |
| Best reference dataset | `npci_upi_product_statistics` | Preferred national monthly benchmark over the contradicted `upi_india_monthly_enriched` ([DATA_PROVENANCE.md](DATA_PROVENANCE.md) §3) |
| Best valid combination | `upi_transactions_2024` (primary) + NPCI bank snapshots (calibration-only, not joined) | [DATASET_ACQUISITION_PLAN.md](DATASET_ACQUISITION_PLAN.md) §G |
| Fields available directly | transaction_id, timestamp, amount, status, payment_method, device, network, sender/receiver bank (8), sender_state (10), fraud_flag | [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) — 12 observed fields |
| Fields derivable | rolling_success_rate, failure_rate, volume, gmv, error_rate, anomaly_score, issuer_bank_full_name | [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) — 8 derived fields |
| Fields requiring proxies | merchant_category (P2M/Bill/Recharge only), region (sender-side only), error-code taxonomy shape (BD/TD) | [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md) |
| Fields requiring synthetic enrichment | gateway_id, routing_path, routing_policy, gateway_latency_ms, gateway_response_code, gateway_health_state, all incident metadata | [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) — 6 synthetic + 6 incident fields |
| Fields impossible to obtain credibly | India-wide/national-scale claims from this sample; real UPI mandate data; any genuinely-verified fine-grained failure taxonomy | [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md) §19E |
| Best demo incident | A synthetically injected, segment-concentrated failure spike at hourly-or-coarser resolution (or densified for a shorter window) | [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §9 |
| Main data limitation | Zero native infrastructure visibility (gateway/routing/latency) and near-uniform native failure variance (no organic incident to detect) | [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md), [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md) |
| Final feasibility | **YES, WITH EXPLICIT CONDITIONS** | [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md) §19 |
