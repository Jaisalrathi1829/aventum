# Data Leakage and Target Leakage Analysis

Checked across every dataset for post-outcome fields, disguised labels, future timestamps, aggregate statistics containing future information, duplicated target information, and derived success/failure information that would invalidate an evaluation.

---

## 1. `upi_transactions_2024` (primary candidate — the dataset any RCA/anomaly model would train against)

| Check | Finding | Severity | Prevention |
|---|---|---|---|
| Post-outcome fields present as if they were pre-outcome features | `fraud_flag` — in real payment systems, fraud determination is typically made **after** a transaction completes (via post-hoc review/rules), not known at authorization time. The file does not state when `fraud_flag` was assigned. | **Medium** | Treat `fraud_flag` as a post-hoc label, never as a real-time input feature to any model that scores transactions before/at the point of `transaction_status` being decided (e.g. do not feed `fraud_flag` into a live anomaly-detection or RCA classifier that is meant to run before/at the moment of outcome). It may be used for retrospective analysis only. |
| Labels disguised as features | None found. `transaction_status` is the only outcome field; no other column encodes it under a different name. | — | — |
| Future timestamps | None. `timestamp` max is 2024-12-30, well before any plausible analysis date. | — | — |
| Duplicated target information | `hour_of_day`, `day_of_week`, `is_weekend` are exact deterministic derivatives of `timestamp` (verified by cross-check — every `is_weekend=1` row's `day_of_week` is Saturday/Sunday). This is **redundancy, not target leakage** (they don't leak `transaction_status`), but including all four (`timestamp` + the three derived columns) as independent features in a model would cause spurious multicollinearity. | **Low** | Derive `hour_of_day`/`day_of_week`/`is_weekend` from `timestamp` on demand in the canonical schema rather than persisting all four as if independently observed; do not double-count them as separate "evidence" in an explanation. |
| Aggregate statistics containing future information | None — this file has no pre-computed rolling/aggregate columns at all (only `upi_india_monthly_enriched` does, see §2). | — | — |
| Derived success/failure information that would invalidate evaluation | None found beyond the `fraud_flag` timing caveat above. | — | — |

**Overall leakage risk for `upi_transactions_2024`: LOW**, with one **medium** caveat (`fraud_flag` timing) that must be documented wherever this field is used.

## 2. `upi_transaction_insights_dataset`

No post-outcome, duplicated-target, or future-timestamp issues found. `is_successful` is the only outcome field; no other column encodes it. The dataset's leakage risk is effectively moot given it is not recommended as an analytical base (per [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md), its `is_successful` is an artificially balanced 50/50 label, not a usable real-world target either way).

## 3. `upi_india_monthly_enriched` — the one file with a real leakage risk

| Check | Finding | Severity | Prevention |
|---|---|---|---|
| Aggregate statistics containing information derived from the same-period target | `Volume_RollMean_3M` / `Value_RollMean_3M` are 3-month rolling means. A standard trailing `rolling(3).mean()` **includes the current month's own `Volume_Mn`/`Value_Cr` value** in its own average. If `Volume_Mn` were ever treated as a "target" to predict or as evidence of an anomaly, using `Volume_RollMean_3M` as a "predictor"/"baseline" in the same row would be **partially predicting the target from itself** — a classic rolling-window leakage pattern. | **Medium-High**, conditional on use | Never use `Volume_RollMean_3M`/`Value_RollMean_3M` from the *same row* as a "pre-anomaly baseline" for that row's own `Volume_Mn`/`Value_Cr` — if a rolling baseline is needed, recompute it as a **strictly trailing, current-value-excluded** window at query time, not by reusing this pre-baked column. |
| Duplicated target information | `Month_Sin`/`Month_Cos` are deterministic encodings of `Month_Num`, not of `Volume_Mn`/`Value_Cr` — exogenous (known in advance), not leakage. | — | — |
| Labels assigned with hindsight | `Is_Covid_Period`, `Is_Festive_Season` are calendar-based flags, knowable in advance independent of the volume/value outcome — not leakage of `Volume_Mn`/`Value_Cr` itself. `Event_Code` is unexplained (no legend found anywhere in the corpus) — treat its meaning as **unverified**, not necessarily as a leakage risk, but do not assume it is exogenous without confirming its derivation. | **Low (unverified)** | Do not use `Event_Code` in any model or explanation until its construction method is confirmed; document it as "meaning unknown" in the data dictionary. |
| Contradiction risk compounding leakage | As established in [DATA_PROVENANCE.md](DATA_PROVENANCE.md) §3, this file's own `Volume_Mn`/`Value_Cr` numbers diverge materially from `npci_upi_product_statistics` for the same months — any leakage-adjacent derived feature (rolling mean, MoM/YoY %) inherits that same unverified base, compounding the risk of treating this file's derived columns as ground truth. | **Medium** | Prefer `npci_upi_product_statistics` as the base series; treat this file's engineered columns as exploratory only. |

## 4. NPCI monthly time-series files (BHIM, Fastag, IMPS, `*99#`, UPI Product Statistics)

No rolling/derived columns exist in these files at all (confirmed — each has only `Month` + 2–3 raw measures). **No leakage risk** — these are raw aggregate observations, not pre-engineered features.

## 5. NPCI single-snapshot entity files (Remitter Banks, Beneficiary Bank, Payers Performance PSP, "mandate creation")

Single cross-sectional snapshot per file — no temporal ordering exists within the file to leak across. `Approved%` + `BD%` + `TD%` are mutually derivable from each other (they are components of the same 100%, confirmed in [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md) to sum to ~100% with minor rounding) — this is **not leakage in the predictive-modeling sense** (there is no separate target being predicted here), but it means these three columns carry only 2 independent degrees of freedom, not 3; do not treat all three as independent evidence when the third can be derived from the other two.

## 6. `npci_year_wise_digital_transaction`, `npci_upi_apps_RAW`

No derived/rolling columns, no outcome field to leak. `npci_upi_apps_RAW`'s "Total" columns are the sum of its own 4 category columns (a structural note, not leakage — see [DATASET_INVENTORY.md](DATASET_INVENTORY.md)); this is a double-counting risk in aggregation, not a target-leakage risk.

---

## Summary

| Dataset | Leakage risk | Primary issue |
|---|---|---|
| `upi_transactions_2024` | **Low** (one medium caveat) | `fraud_flag` timing — treat as post-hoc, not a live-scoring feature |
| `upi_transaction_insights_dataset` | Low / moot | Not used as an analytical base |
| `upi_india_monthly_enriched` | **Medium-High if misused** | Rolling-mean columns include the current period's own value; do not use as a same-row "baseline" for that period's own volume/value |
| NPCI monthly time-series (5 files) | None found | Raw aggregates, no engineered columns |
| NPCI snapshot entity files (4 files) | None found (structural non-independence noted, not leakage) | Approved/BD/TD are 2 degrees of freedom, not 3 |
| `npci_year_wise_digital_transaction`, `npci_upi_apps_RAW` | None found | "Total" double-counting is an aggregation trap, not leakage |

**No dataset in this corpus contains a hidden or disguised copy of a prediction target.** The one real risk (`upi_india_monthly_enriched`'s rolling-mean columns) is avoidable by recomputing rolling baselines at query time rather than reusing the pre-baked columns, and the one caveat worth carrying forward operationally (`fraud_flag` timing in `upi_transactions_2024`) is a documentation requirement, not a blocker.
