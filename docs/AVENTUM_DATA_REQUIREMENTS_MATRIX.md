# Aventum Data Requirements Matrix

This is the central Day 1 decision document. It evaluates every capability Aventum needs against the actual datasets, using the findings already established in [DATASET_GRAIN_ANALYSIS.md](DATASET_GRAIN_ANALYSIS.md), [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md), [DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md), and [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md) (not re-derived here). Primary dataset throughout: `upi_transactions_2024` (250,000 rows, transaction grain, 2024-01-01→2024-12-30). Classification scale:

1. **DIRECTLY SUPPORTED** — field/capability exists as-is
2. **SUPPORTED THROUGH DERIVATION** — computable deterministically from observed fields
3. **SUPPORTED THROUGH A VALID PROXY** — a different field legitimately stands in
4. **SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** — requires the synthetic infrastructure/incident layer
5. **NOT CREDIBLY SUPPORTED** — no credible path with current data even with reasonable enrichment

---

## A. Payment Monitoring

| Requirement | Source column(s) | Grain/resolution | Classification | Limitation |
|---|---|---|---|---|
| Overall transaction volume | `upi_transactions_2024` (row count) | transaction, second | **1. DIRECTLY SUPPORTED** | Represents ~1 in 705,800 of claimed national volume ([DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §3) — a sample dataset, not the ecosystem; frame all volume claims as "within this dataset," never "India's UPI volume." |
| GMV | `amount (INR)` | transaction | **2. SUPPORTED THROUGH DERIVATION** | Sum is well-defined; no currency-conversion or negative-amount issues (min 10, max 42,099, confirmed clean). |
| Success rate / failure rate | `transaction_status` | transaction | **2. SUPPORTED THROUGH DERIVATION** | 95.05% success / 4.95% failure overall — realistic order of magnitude. |
| Temporal trends | `timestamp` | second→day reliably, sub-hour noisy | **2. SUPPORTED THROUGH DERIVATION** | See §9 Temporal Feasibility — density collapses below ~hourly resolution. |
| Payment-method trends | `transaction type` (P2P/P2M/Bill Payment/Recharge) | transaction | **2. SUPPORTED THROUGH DERIVATION** | Clean 4-category field, 0 nulls. |
| Bank/issuer trends | `sender_bank`/`receiver_bank` | transaction | **2. SUPPORTED THROUGH DERIVATION** | Only 8 of ~60 real Indian UPI-participating banks are represented ([DATASET_JOIN_ANALYSIS.md](DATASET_JOIN_ANALYSIS.md) §2) — a fixed, narrow bank universe. |
| Merchant/category trends | `merchant_category` | transaction | **3. SUPPORTED THROUGH A VALID PROXY** | Populated even for P2P rows where no real merchant exists ([FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)) — valid only for P2M/Bill Payment/Recharge subsets. |
| Geographic trends | `sender_state` | transaction | **3. SUPPORTED THROUGH A VALID PROXY** | Sender-side only, 10 of 28+ states; no receiver geography. |
| Device/network trends | `device_type`, `network_type` | transaction | **1. DIRECTLY SUPPORTED** | Clean, 0 nulls, realistic proportions. |

## B. Anomaly Detection

The *mechanism* (rolling-window statistics, deviation-from-baseline scoring) is derivable from `upi_transactions_2024` for every dimension below. The *substance* — whether a genuine, detectable anomaly exists to demonstrate — depends on the failure-rate-variance finding in [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md): native failure-rate spread across every dimension is 0.2–0.9 percentage points against a 4.95% base, i.e., statistically close to noise. This is called out per row.

| Requirement | Mechanism source | Classification | Reasoning |
|---|---|---|---|
| Sudden success-rate deterioration | `transaction_status` + `timestamp` | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Rolling-rate computation is derivable, but no genuine deterioration exists natively to detect — see §9. A believable demo requires an injected synthetic incident window. |
| Failure-rate spikes | same | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Same reasoning. |
| Volume anomalies | `timestamp` (count) | **2. SUPPORTED THROUGH DERIVATION** | Native day-of-week/seasonal volume pattern exists and is real (weekday vs weekend split observed); a genuine volume *drop/spike* anomaly (vs. normal seasonal variation) is not present natively and would need synthetic injection for a demo — borderline 2/4 depending on whether "anomaly" means organic outlier days (some exist, min 595 vs max 772 per day, a ~30% band) or a deliberate incident (needs injection). |
| Issuer-specific anomalies | `sender_bank` | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Native per-bank failure spread only 0.28–0.51 pts — no bank is a natural outlier. |
| Payment-method-specific anomalies | `transaction type` | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Same reasoning class. |
| Merchant/category anomalies | `merchant_category` | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Same reasoning class, compounded by the P2P proxy caveat above. |
| Latency anomalies | none | **5. NOT CREDIBLY SUPPORTED** (native); **4. after enrichment** | No latency field or proxy exists anywhere in the corpus ([FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)) — fully synthetic field required before this capability can exist at all. |
| Error-code anomalies | none | **5. NOT CREDIBLY SUPPORTED** (native); **4. after enrichment** | No error-code field exists; NPCI BD%/TD% split is only a proportion seed, not a per-row label. |
| Gateway anomalies | none | **5. NOT CREDIBLY SUPPORTED** (native); **4. after enrichment** | No gateway field or proxy exists at all. |

## C. Root-Cause Analysis (correlating failure across dimensions)

| Correlate against | Classification | Reasoning |
|---|---|---|
| Gateway | **5. NOT CREDIBLY SUPPORTED** → 4 after enrichment | No field, no proxy. |
| Payment method | **2. SUPPORTED THROUGH DERIVATION** (mechanism); weak native signal | Correlation is computable; native effect size is small. |
| Issuer/bank | **2. SUPPORTED THROUGH DERIVATION** (mechanism); weak native signal | Same; NPCI proxy confirms real banks *can* show much larger effect sizes (Central Bank of India 53.47% TD%) than this dataset does natively. |
| Error code | **5. NOT CREDIBLY SUPPORTED** → 4 after enrichment | No field, no proxy. |
| Time | **2. SUPPORTED THROUGH DERIVATION** | `timestamp` at second resolution, fully usable. |
| Merchant/category | **3. SUPPORTED THROUGH A VALID PROXY** (mechanism); weak native signal | P2P caveat applies. |
| Geography | **3. SUPPORTED THROUGH A VALID PROXY** (mechanism); weak native signal | Sender-state only. |
| Device/network | **2. SUPPORTED THROUGH DERIVATION** (mechanism); weak native signal | Spread 0.22–0.36 pts natively. |
| Transaction attributes (amount, type) | **2. SUPPORTED THROUGH DERIVATION** | Fully available. |

**Analytical-sufficiency conclusion for RCA specifically:** the correlation *machinery* works end-to-end on real fields for 6 of 9 dimensions. But because native failure variance is close to uniform everywhere, an RCA demo running against unmodified `upi_transactions_2024` would correctly conclude "no dominant root cause" — which is truthful but not a usable demo. A credible RCA demonstration requires a synthetically injected incident with a real, concentrated effect size, at which point the same derivation machinery becomes fully capable of finding it.

## D. Explanation

| Requirement | Classification | Reasoning |
|---|---|---|
| Before/after success rate | **2. SUPPORTED THROUGH DERIVATION** (mechanism) | Needs a real onset boundary (native or synthetic) to be meaningful — see §9. |
| Affected volume | **2. SUPPORTED THROUGH DERIVATION** | Straightforward count/filter once a segment+window is defined. |
| Concentrated affected segment | **2. SUPPORTED THROUGH DERIVATION** (mechanism); **4** for a genuine concentration | Native segments are near-uniform; a genuinely "concentrated" segment requires synthetic injection. |
| Error-rate change | **2. SUPPORTED THROUGH DERIVATION** (mechanism), gated by same caveat | — |
| Latency change | **5. NOT CREDIBLY SUPPORTED** → 4 after enrichment | No latency field exists at all. |
| Unaffected comparison group | **2. SUPPORTED THROUGH DERIVATION** | Any other bank/device/state segment can serve as a control group — mechanically sound regardless of native vs. synthetic incident. |
| Temporal onset | **2. SUPPORTED THROUGH DERIVATION** (mechanism); reliability gated by density | At native density (12.46% of 10-min windows are empty, 51.7% have <5 transactions — [DATA_QUALITY_REPORT.md](DATA_QUALITY_REPORT.md)) onset detection below ~1-hour resolution is unreliable; hourly+ is workable. |

## E. Counterfactual Simulation

| Requirement | Classification | Reasoning |
|---|---|---|
| Replay historical transactions | **1. DIRECTLY SUPPORTED** | Real, event-grain rows exist and can be iterated/replayed as-is. |
| Preserve transaction characteristics | **1. DIRECTLY SUPPORTED** | All 17 observed fields carry through a replay unchanged. |
| Alter routing hypothetically | **5. NOT CREDIBLY SUPPORTED** natively → **4. after enrichment** | There is no routing/gateway field to alter — a "different gateway" is not a perturbation of observed data, it is a wholly invented construct requiring a synthetic gateway model as a prerequisite ([FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)). |
| Compare alternative routing policies | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Requires the same synthetic gateway model, applied under ≥2 parameter sets. |
| Estimate success-rate difference | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | The estimate is a model output conditioned on assumed synthetic-gateway success curves — must be labeled "simulated," never "observed." |
| Estimate recovered transactions | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Same — derived from the same simulated success-rate difference applied to affected volume. |
| Estimate GMV impact | **2. SUPPORTED THROUGH DERIVATION**, gated by E above | GMV arithmetic itself (recovered transactions × observed amount distribution) is simple derivation once a recovered-transaction estimate exists; the estimate it depends on is synthetic-gated. |
| Compare multiple intervention sizes | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Needs the synthetic outcome model to vary traffic-percentage inputs against. |
| Identify downside/risk | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | No historical routing-change event exists anywhere in the corpus to calibrate a real risk curve from; risk must be modeled, not measured. |

**Mandatory separation (see also §11 below):** every counterfactual output must be tagged as **simulated**, distinct from the **observed** transaction it was replayed from, and distinct from the **model assumption** (e.g., an assumed synthetic-gateway success rate) that produced it. This separation is enforced in the canonical schema — see [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md).

## F. Recovery Recommendation

| Requirement | Classification | Reasoning |
|---|---|---|
| Target segment | **2. SUPPORTED THROUGH DERIVATION** | Any combination of real observed dimensions (bank × device × state × category, etc.) — see §10 Segmentation Feasibility for which combinations are statistically usable. |
| Target gateway | **5. NOT CREDIBLY SUPPORTED** → 4 after enrichment | No gateway field/proxy exists. |
| Traffic percentage | **Governed by the deterministic safety/policy engine, not derived from data** | The value itself is a bounded policy decision (per the project's explicit LLM-is-not-authoritative principle), not something the dataset can supply — the dataset only supplies the *volume* the percentage would apply to. |
| Duration | Same as traffic percentage | Policy-engine bounded, not data-derived. |
| Expected benefit | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Downstream of the Counterfactual Simulation outputs in §E. |
| Expected GMV impact | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Same. |
| Confidence | **2. SUPPORTED THROUGH DERIVATION**, gated by E | Statistical confidence intervals are standard math once a simulation exists; the simulation itself is synthetic-gated. |
| Risk | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Same reasoning as Identify downside/risk in §E — no real calibration data exists. |

## G. Verification

| Requirement | Classification | Reasoning |
|---|---|---|
| Pre-action baseline | **2. SUPPORTED THROUGH DERIVATION** | Rolling success-rate/volume computation is standard, works on real or synthetic-incident data alike. |
| Post-action outcome | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** (for the Day 1 static-CSV prototype) | `upi_transactions_2024` is a closed, static historical file — no real "action" was ever taken against it, so there is no real continuation to observe. A post-action outcome can only exist for (a) a synthetically continued incident window in this prototype, or (b) a genuinely live transaction feed in a future, non-Day-1 system. |
| Recovery magnitude | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Same — depends on the synthetic post-action continuation. |
| Recovery speed | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Same. |
| Rollback trigger | **2. SUPPORTED THROUGH DERIVATION** | A threshold rule on a derived rolling metric — mechanically independent of whether the underlying data is real or synthetic. |
| Intervention effectiveness | **4. SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT** | Downstream of post-action outcome. |

---

## 8. Analytical Sufficiency — "field exists" vs. "field is usable"

Explicit distinctions, each backed by a computed number:

- `sender_bank`/`receiver_bank` **exist** at transaction grain (250,000 labeled rows) — but only span **8 banks**, so bank-level anomaly detection is only ever usable across those 8, never generalizable to "any Indian bank."
- `sender_state` **exists** at transaction grain — but only spans **10 of 28+ states/UTs**, so any geographic claim is bounded to that subset, not "India-wide."
- Bank-level decline-rate benchmarks (`BD%`/`TD%`) **exist** in NPCI reference files — but only as a **single Sep-2023 cross-sectional snapshot** (confirmed: no repeated-bank time series in those files, per [DATASET_GRAIN_ANALYSIS.md](DATASET_GRAIN_ANALYSIS.md)), so they cannot support minute-level, or even month-level, per-bank incident *detection* — only a one-time realism check for calibrating synthetic parameters.
- `timestamp` **exists** at second resolution — but transaction *density* (~4.76/10-min window, 12.46% empty windows) means sub-hourly statistics computed from it carry high sampling noise; the field's resolution exceeds what the volume can actually support analytically.
- `merchant_category` **exists** for every row — but is only **semantically meaningful for ~55% of rows** (P2M/Bill Payment/Recharge; 112,445/250,000 = 45% are P2P rows where the category is not applicable), so any category-based claim must explicitly scope to the applicable transaction types.
- Failure-rate variance **exists** across every dimension (bank, device, network, hour, etc.) — but at 0.2–0.9 percentage points against a 4.95% base rate, it is **not large enough to be analytically distinguishable from sampling noise** at the segment sizes present (smallest segment ~600 rows; a 0.9-point difference on a ~5% base rate at n≈600 is within one standard error). "The column exists and varies" is not the same as "the variation is a usable signal."

## 9. Temporal Feasibility

Finest meaningful resolution, measured directly from `upi_transactions_2024` (`audit_scripts/deep_analysis.py` §C):

| Resolution | Avg transactions | Distribution | Realistically usable for anomaly detection? |
|---|---|---|---|
| Per transaction (event) | 1 | — | Yes, for replay/evidence, not for statistics |
| Per 10 minutes | 4.76 | 12.46% of windows empty, 51.7% have <5 txns | **No** — indistinguishable from noise at this density |
| Per hour | 28.5 | min 0, median 29, max 82 | **Borderline** — usable only for a large-effect-size synthetic incident, not subtle deterioration |
| Per day | 686.8 | min 595, max 772 (a ~30% natural band) | **Yes** — the most reliable native resolution |
| Per month | ~20,833 | stable | Yes, but too coarse for "sudden incident" framing |

**Explicit answer to the brief's demo question:**

> *"Can we genuinely demonstrate a 10-minute incident?"* — **No, not from organic transaction density alone.** At ~4.76 transactions per 10-minute window with 12.46% of windows containing zero transactions, a real statistical test cannot reliably distinguish a genuine 10-minute deterioration from ordinary Poisson-like noise (√4.76 ≈ 2.18 is a large fraction of the mean itself).
>
> *"Can we demonstrate a daily anomaly?"* — **Yes**, day-level aggregation (686.8 ± modest natural variation) gives enough volume per window for a statistically legible before/after comparison, and this is the safest native resolution to build on.
>
> **Recommended demo design implication:** either (a) frame the incident at **hourly-or-coarser** resolution using native density, or (b) have the synthetic infrastructure/incident layer **densify transaction volume specifically within the injected incident window** so that a shorter (e.g., 10–30 minute) incident has enough synthetic volume to be statistically legible — and disclose that densification as synthetic, not organic.

## 10. Segmentation Feasibility

2-way intersections tested computationally (`audit_scripts/deep_analysis.py` §D), full-year totals:

| Segment intersection | Cells | Min cell size | Sparse (<30) | Sparse (<100) |
|---|---|---|---|---|
| bank × device | 24 | 1,025 | 0 | 0 |
| bank × state | 80 | 1,591 | 0 | 0 |
| bank × merchant_category | 80 | 615 | 0 | 0 |
| device × network | 12 | 636 | 0 | 0 |
| bank × network | 32 | 991 | 0 | 0 |
| state × merchant_category | 100 | 596 | 0 | 0 |
| bank × transaction_type | 32 | 984 | 0 | 0 |

**At whole-dataset (full-year) grain, every tested 2-way segment intersection is statistically usable** — zero sparse cells even at a 100-row threshold. This is favorable and was not assumed; it was measured.

**Critical caveat, not visible from the table above:** these cell counts are for the **entire 364-day dataset**. Segmentation and short-time-window incident detection are in tension: the same `bank × device` cell that averages 1,025+ rows across the full year averages only **~2.8 rows/day** (1,025 ÷ 364) and a small fraction of that per hour. **A demo cannot simultaneously claim fine time resolution (e.g., "detected within 10 minutes") AND fine segment resolution (e.g., "isolated to Bank X on Android on 4G") using native density — one of the two axes must be coarsened, or the incident window must be synthetically densified (per §9).** High-volume segments (`sender_bank` alone, `device_type` alone) remain viable at finer time windows than deep 3-way intersections; 3-way and finer intersections were not tested because even the 2-way baseline already establishes this ceiling.

## 11. Counterfactual Simulation Feasibility

Answering the brief's 9 explicit questions, grounded in §E and [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md):

1. **Can historical transactions be replayed?** Yes — 250,000 real, distinct, fully-attributed rows exist and can be iterated deterministically.
2. **Which attributes can be preserved?** All 17 observed fields (bank, device, network, amount, category, state, timestamp, status) carry through a replay unmodified.
3. **Can routing be modified hypothetically?** Only as a pure synthetic overlay — there is no observed routing/gateway field to perturb; "alternate routing" must be entirely invented and clearly labeled as such.
4. **Can alternate outcomes be estimated from observed information?** Only weakly and indirectly — observed per-segment success rates (e.g., "SBI transactions succeed 94.9% of the time in this dataset") are legitimate priors for a probabilistic outcome model, but the counterfactual itself ("on gateway B") references an entity that was never observed, so the estimate is model output, not measurement.
5. **Which model/assumptions would be required?** A per-segment baseline success-rate model (data-supported); an assumed relationship between a synthetic gateway's modeled health/capacity and transaction outcome; an assumed latency distribution per synthetic gateway.
6. **Which assumptions are directly supported by data?** The baseline segment-level success/failure frequencies (bank, device, network, category, hour) — these are real, measured frequencies.
7. **Which assumptions require synthetic infrastructure?** Gateway existence, gateway-specific success-rate curves, gateway capacity/health state, and the causal link from "route here" to "this outcome."
8. **Which counterfactual results would be defensible?** Directional, clearly-labeled comparisons under transparent synthetic parameters — e.g., "if a synthetic alternate gateway is modeled at 98% success versus the current degraded segment's actual 80%, shifting N% of traffic would be projected to recover approximately X transactions," presented explicitly as a simulation.
9. **Which would be too speculative?** Any claim that omits the synthetic-assumption caveat, or that implies the recovery number reflects real historical bank/gateway behavior — since no real alternate-gateway outcome was ever observed in this corpus, that framing would misrepresent a modeled projection as measured fact.

**Required category separation, enforced going forward:** *Observed outcome* (the real `transaction_status` in the data) ≠ *simulated counterfactual outcome* (a modeled probability under synthetic gateway assumptions) ≠ *model assumption* (the chosen synthetic parameters themselves, e.g. "assumed 98% success rate for synthetic Gateway B"). These three must never be rendered identically in any Aventum output.

## 12. Ground-Truth Feasibility

No raw dataset in this corpus marks any time window, transaction subset, or bank/segment as a labeled "incident" with a stated cause, onset, or resolution. The closest available evidence is the NPCI Sep-2023 cross-sectional snapshot showing Central Bank of India at 53.47% TD% — a real, extreme, single-point-in-time observation with **no time-boundedness, no onset, no resolution, and no causal narrative**, so it can only serve as an *existence proof* that such events are realistic in real Indian UPI infrastructure, never as an actual ground-truth label for any specific transaction or window (full reasoning in [FIELD_PROXY_ANALYSIS.md](FIELD_PROXY_ANALYSIS.md)).

**Conclusion: ground truth for failure reasons, bank/gateway degradation, and time-bounded incidents must be introduced entirely by the controlled synthetic incident layer.** The final system must keep these six categories distinct and never blend them:

```text
Observed historical fact        (from upi_transactions_2024, as-is)
Synthetic incident ground truth (introduced by Aventum's own incident-injection layer, labeled synthetic)
Derived analytics               (deterministically computed from observed/synthetic facts)
Agent hypothesis                (LLM-generated interpretation, never authoritative on numbers)
Agent recommendation            (LLM-proposed action, bounded by the deterministic safety/policy engine)
Observed post-action outcome    (only real for a live feed; synthetic-continuation for the Day 1 static prototype)
```

This separation is carried into the field-level `observed/derived/synthetic/incident` tagging required in [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md).
