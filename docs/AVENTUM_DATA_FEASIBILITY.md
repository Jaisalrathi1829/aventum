# Aventum Data Readiness Score and Final Feasibility Decision

This document closes out Day 1: a justified readiness score (brief §18) followed by the final feasibility decision (brief §19). Every score and claim below traces to a specific finding already established in [DATASET_INVENTORY.md](DATASET_INVENTORY.md), [DATASET_GRAIN_ANALYSIS.md](DATASET_GRAIN_ANALYSIS.md), [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md), [DATA_PROVENANCE.md](DATA_PROVENANCE.md), [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md), [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md), [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md), and [DATA_LEAKAGE_ANALYSIS.md](DATA_LEAKAGE_ANALYSIS.md) — not re-derived here.

---

## 18. Aventum Data Readiness Score

| Dimension | Score /10 | Justification |
|---|---|---|
| Transaction richness | 7 | 17 fields on `upi_transactions_2024` spanning bank, device, network, category, geography, amount, status, time — but zero gateway/latency/error-code fields ([FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)). |
| Transaction-level availability | 9 | 250,000 real event-grain rows, full calendar year, 0 nulls, 0 duplicates ([DATASET_GRAIN_ANALYSIS.md](DATASET_GRAIN_ANALYSIS.md)). |
| Temporal resolution | 6 | Second-precision timestamps exist, but usable statistical resolution collapses below ~1 hour (12.46% of 10-min windows are empty — [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §9). |
| Success/failure information | 6 | Clean binary `transaction_status` at a realistic 95.05%/4.95% split — but only two states, no reason codes. |
| Amount/GMV information | 9 | Clean numeric `amount (INR)`, no negatives/zeros, realistic right-skewed shape. |
| Issuer/bank coverage | 4 | Only 8 of India's ~50–60 real UPI-participating banks are represented ([DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §2). |
| Payment-method coverage | 8 | 4 clean, well-populated categories (P2P/P2M/Bill Payment/Recharge). |
| Segmentation depth | 7 | Every tested 2-way segment intersection is statistically usable at full-year grain (0 sparse cells even at n<100) — but this collapses sharply inside narrow incident time windows ([AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §10). |
| Merchant/category coverage | 5 | Present for every row, but only semantically valid for the 55% of rows that are not P2P ([FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)). |
| Geography | 4 | Sender-side only, 10 of 28+ states/UTs, no receiver geography, no city/pincode. |
| Device/network information | 8 | Clean, realistic device (Android/iOS/Web) and network (4G/5G/WiFi/3G) fields. |
| Failure reason/error information | 2 | No per-transaction reason code anywhere; only a binary outcome. NPCI files provide an aggregate BD%/TD% *proportion* reference only. |
| Gateway visibility | 1 | No gateway field or proxy exists anywhere in the 14-dataset corpus. |
| Latency visibility | 1 | No latency/processing-time field or proxy exists anywhere. |
| Routing visibility | 1 | No routing-path/routing-policy field or proxy exists anywhere. |
| Joinability | 3 | Only one join (bank-name, with a 2-entry manual alias table) is even mechanically clean, and it is still classified HIGH-RISK for row-level use ([DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md)); most other combinations are INVALID or NOT NEEDED. |
| Incident suitability (native) | 3 | No dataset marks any real incident; native failure-rate variance across every dimension is 0.2–0.9 points against a 4.95% base — statistically close to noise ([DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md)). |
| RCA suitability | 4 | The correlation machinery works end-to-end on 6 of 9 required dimensions, but native effect sizes are too small to produce a meaningful finding without synthetic injection. |
| Counterfactual suitability | 3 | Replay and attribute-preservation are fully supported; the routing/gateway dimension being altered does not exist at all, so the counterfactual's subject matter is entirely synthetic. |
| Verification suitability | 3 | Pre-action baseline computation is straightforward; post-action outcome is structurally unavailable against a static historical CSV with no live continuation ([AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §G). |

**Overall Aventum Data Readiness: 4.7/10** (unweighted mean of the 20 scores above).

This is a deliberately unflattering-but-honest number, not a rounding-friendly one. It reflects a corpus that is **strong on the transaction-monitoring half of Aventum** (richness, volume, amount, method, device/network all score 6–9) and **structurally near-empty on the infrastructure-diagnosis half** (gateway/latency/routing all score 1, failure-reason scores 2) — which is exactly the half the project brief identifies as Aventum's actual differentiator versus a generic router. The score is not a verdict against building Aventum; it is a precise statement of **how much of the system must be built on synthetic foundations**, which the feasibility decision below addresses directly.

---

## 19. Final Feasibility Decision

### A. What can be built using ONLY observed public data?

- Payment-monitoring views (volume, GMV, success/failure rate, trends by bank/device/network/payment-method, partial category and geography) scoped honestly to `upi_transactions_2024`'s 8-bank, 10-state universe.
- Cross-sectional benchmarking commentary from the NPCI reference files, cited separately, never merged into the transaction table.
- Basic segmentation views (the 7 tested 2-way intersections) at daily-or-coarser resolution.

### B. What can be built using observed + derived data?

- Rolling success/failure-rate and volume/GMV analytics computed at daily-or-coarser resolution.
- The full RCA correlation *mechanism* across payment method, bank, device, network, time, category, and geography — mechanically complete, though the native signal it finds will correctly report "no dominant cause" absent a synthetic incident.
- Explanation-evidence assembly (affected volume, comparison group, before/after framing) once a genuine or synthetic incident window is defined.

### C. What requires synthetic infrastructure enrichment?

- The entire gateway/routing/latency/gateway-health/error-code dimension — zero proxy exists for any of it ([FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)).
- A genuinely detectable anomaly for demo purposes — native failure variance is noise-level everywhere tested.
- Counterfactual routing simulation in its entirety (Requirements Matrix §E).
- Recovery-recommendation fields tied to gateway targeting and projected benefit/GMV impact/risk (Requirements Matrix §F).

### D. What requires synthetic incident ground truth?

- `incident_id`/`incident_start`/`incident_end`/`incident_type`/`affected_segment`/`ground_truth_root_cause` — used only for offline evaluation of Aventum's own output, never as pipeline input (Requirements Matrix §12).
- Post-action verification for the Day 1 static-CSV prototype, since no live feed exists to observe a real continuation.

### E. What cannot be credibly built even with reasonable enrichment?

- Any claim that the system reflects **India's actual national UPI ecosystem** — the transaction sample represents roughly 1 in 705,800 of the claimed national monthly volume ([DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §3) and covers only 8 of ~60 banks; no amount of synthetic enrichment turns a fixed 8-bank/10-state sample into a national census without misrepresenting it.
- A verified, cited real-world root-cause taxonomy beyond the coarse Bank-Declined/Technical-Declined split already present in NPCI reference data — anything finer must be invented, not discovered.
- Genuine UPI *mandate* (recurring-payment) analytics of any kind — the one file with that filename is confirmed mislabeled and contains remitter-bank data instead ([DATASET_INVENTORY.md](DATASET_INVENTORY.md)).

### F. Minimum dataset stack required

`upi_transactions_2024` alone, plus a synthetic infrastructure layer (gateway/routing/latency) and a synthetic incident-injection layer built on top of it. This is the smallest stack that lets every core Aventum stage (Detect→Diagnose→Explain→Simulate→Recommend→Approve→Execute→Verify→Audit) execute end-to-end, even if every infrastructure/incident value in it is synthetic.

### G. Strongest dataset stack available from what we have

`upi_transactions_2024` (primary transaction backbone) + `npci_upi_remitter_banks` / `npci_upi_beneficiary_bank` / `npci_upi_payers_performance_psp` (BD%/TD% proportion calibration for synthetic error-code generation, and a realistic-magnitude reference for how large a real bank-specific degradation can get) + `npci_upi_product_statistics` (national-trend context, cited separately, never merged) + the same synthetic infrastructure/incident layer as in F. `upi_india_monthly_enriched` and `upi_transaction_insights_dataset` are available but deliberately excluded from this "strongest" stack per their documented low-confidence/low-volume limitations.

### H. Data assumptions that must be stated to judges

1. `upi_transactions_2024` is assessed as **synthetic** (not confirmed real operational data) — it is circumstantially, but not confirmedly, linked to a personal Power BI portfolio project's dashboard ([DATA_PROVENANCE.md](DATA_PROVENANCE.md)).
2. The dataset covers **8 banks and 10 Indian states only** — it is a sample, not a national census, and must never be described as "India's UPI data" without that qualifier.
3. Native failure-rate variance across every dimension is close to uniform (0.2–0.9 points on a 4.95% base) — **any incident shown in a live demo is a deliberately injected synthetic scenario**, and must be disclosed as such at the moment it is shown.
4. Gateway, routing, latency, and error-code data are **entirely synthetic constructs with no basis in any real dataset** — and UPI's real architecture is a single national switch (NPCI), not a multi-gateway network like card processing, so the "alternate gateway routing" framing is itself a deliberate simplification for demonstration purposes, not a claim about real UPI infrastructure.
5. NPCI reference files are **plausibly real published statistics but are uncited within the files themselves** — treat as medium-confidence, not verified.
6. `upi_india_monthly_enriched` materially contradicts the NPCI file it should agree with for the same months — it must never be cited as ground truth for historical UPI volume.

### I. What analytical claims must we NOT make?

- "Aventum recovered ₹X in real GMV" for any demo scenario — no real recovery ever occurred against static, closed historical data; any such number is a labeled simulation output.
- Any claim that this system's findings represent India's national UPI failure patterns.
- Any claim that gateway/routing/latency/error-code values are observed rather than synthetic.
- Any claim that a demo-shown incident is a real historical event rather than a disclosed synthetic injection.
- Any claim, statistic, or product feature built on `UPI mandate creation.csv` being about UPI mandates.

### J. Can the intended Aventum demo be built without fabricating the core evidence?

**Yes** — the transaction backbone (`upi_transactions_2024`) is genuine event-grain data with realistic structure and 250,000 real, distinct, fully-attributed rows, which is sufficient to run the entire monitoring/detection/derivation machinery honestly. The gap is not in the *mechanism* Aventum needs — it is in one clearly-bounded dimension (infrastructure + incident ground truth) that the project brief already anticipates addressing synthetically. As long as that synthetic layer is built, labeled, and disclosed exactly as specified in §H/§I above, no core evidence needs to be fabricated to build a credible demo.

---

## Final Decision

# YES, WITH EXPLICIT CONDITIONS

**Conditions (all previously established in this audit, restated as the operating conditions for Day 2+):**

1. Every synthetic field (gateway, routing, latency, gateway health, error code, incident metadata) must carry a `synthetic`/`incident` provenance tag in the canonical schema and must never be rendered indistinguishably from observed fields ([AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md)).
2. Any demo incident is a disclosed synthetic injection, not a claim of real historical failure.
3. All volume/coverage claims are scoped to the actual 8-bank/10-state/250K-row sample — never generalized to "India" or "the national ecosystem."
4. `upi_india_monthly_enriched` is never cited as ground truth without the contradiction caveat; `npci_upi_product_statistics` is preferred where a national monthly benchmark is needed.
5. `UPI mandate creation.csv` is never used or cited as mandate data.
6. No dataset combination classified INVALID or HIGH-RISK in [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) is physically merged; the NPCI bank-snapshot files are used only for synthetic-parameter calibration, never as a row-level join onto `transactions`.
7. `incident.*` ground-truth fields are used only for offline evaluation of Aventum's output, never fed into its diagnosis pipeline as input.
