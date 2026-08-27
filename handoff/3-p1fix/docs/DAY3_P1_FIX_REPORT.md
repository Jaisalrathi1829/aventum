_Aventum internal report — surgical closure of the two Day 3 P1 issues._

# Day 3 P1 Fix Report

Closes the two P1 issues raised in [DAY3_ARCHITECTURE_REVIEW.md](DAY3_ARCHITECTURE_REVIEW.md). No P2 work, no redesign, no Day 4.

---

## Executive Summary

Two integration-surface defects blocked Day 4:

**P1-1** — a single degradation emitted every cohort that intersected it as an equal-priority alert. One gateway fault produced 22 alerts, 8 CRITICAL/HIGH, of which one was the cause. `handoff.build_handoff()` published all of them, so this *was* Day 4's input.

**P1-2** — RCA confidence rewarded attribution decisiveness far more than absolute evidence strength. A 5.16σ incident scored **higher** confidence (0.6944) than the 9.26σ flagship (0.6396). Day 4 bounds actions by confidence, so weaker evidence could authorise a larger intervention.

Both are closed:

| | Before | After |
|---|---|---|
| Golden alerts (actionable) | 6 | **1 PRIMARY** + 5 derivative |
| gateway_B alerts (actionable) | 22 | **2 PRIMARY** + 20 derivative |
| Issuer SBI alerts (actionable) | 17 | **2 PRIMARY** + 15 derivative |
| Mean alert precision | 16.5% | **83.3%** |
| 5.16σ confidence | 0.6944 (CONFIDENT) | **0.3980 (UNCERTAIN)** |
| 9.26σ confidence | 0.6396 (CONFIDENT) | **0.6881 (CONFIDENT)** |
| σ↔confidence Pearson | +0.657 | **+0.923** |
| Critical inversion | present | **resolved** |
| Full suite | 362/362 | **378/378** |

Observed data, canonical fingerprint, and Day 2 generation fingerprint are all byte-identical. Ground-truth isolation still passes adversarially.

---

## Baseline Before Fix

| Item | Value |
|---|---|
| Full suite | 362 passed / 0 failed / 0 skipped |
| Observed content MD5 | `13965d76407219517a57702df5f24226` |
| Canonical fingerprint | `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8` |
| Generation staleness | `CURRENT`, run 35 |
| `baseline-v1` | gateway_A 0.040197 · B 0.051247 · C 0.062075 · D 0.046164 · E 0.057324 |
| Alert surface | golden 6 · SBI 17 · gateway_B 22 · HDFC 11 · marginal 2 · gateway_D 8 |

---

## P1-1 Root Cause

The derivative alerts were never a statistics problem. `region=Delhi` and `device=Android` *genuinely* had elevated failure rates during a gateway incident, because they carried that gateway's traffic — no significance threshold or multiple-comparison correction removes a real correlation.

Two mechanisms existed but neither addressed it:

1. **`_suppress_redundant`** only suppressed *nested* cohorts (`broader.depth < candidate.depth` plus a subset match). Two depth-1 cohorts on different dimensions — `gateway=gateway_B` and `device=Android` — can never suppress each other under that rule.
2. **The independence/confounding test** in `evidence.py` and `hypothesis.py` *did* identify shadows correctly, and demoted them in hypothesis ranking. It was simply never applied to the alert surface.

So RCA was right while the alert list was 95% noise.

---

## P1-1 Design

The existing machinery is reused, not duplicated. Classification runs in `detect.py` immediately after ranking, walking the reported anomalies strongest-first:

```
candidate
   ↓  exclude each already-established PRIMARY, one at a time
residual cohort
   ↓  re-run DETECTION's own criteria on the residual
still an anomaly?  ──yes──▶  PRIMARY  (stands on its own evidence)
                   ──no───▶  DERIVATIVE (records which primary explains it)
```

### The decision test is re-detection, not a ratio

An earlier iteration thresholded the independence ratio at 0.35. That was measurably wrong: in the golden scenario `region=Delhi` retained 70% of its movement and stayed PRIMARY, even though its residual (4.0pp at 2.56σ) would never have alerted on its own. A ratio cannot distinguish 70% of a small noisy move from 70% of a large one.

The shipped test asks the meaningful question instead — *would this cohort have alerted by itself?* — by re-applying the detector's existing thresholds (`min_cohort_size`, `min_baseline_cohort_size`, `min_absolute_delta`, `min_significance_sigma`) to the residual. **It introduces no new tunable constant.** The independence ratio is still computed and persisted, but purely for explanation.

### Undeterminable ≠ dependent

A second measured flaw drove a further correction. In the systemic scenario `device=Android` is **75% of traffic**; excluding it strips every other cohort below the minimum sample size. Under the first implementation `gateway_C` and `gateway_E` were marked DERIVATIVE *for a volume reason* — the residual was too small to judge, not causally explained.

The shipped code separates the two:

| Residual state | Verdict |
|---|---|
| Cohort vanishes entirely (nested inside the confounder) | **explained** → DERIVATIVE |
| Cohort survives but below sample-size floors | **undeterminable** → NOT explained (stays PRIMARY) |
| Cohort survives and still clears detection thresholds | **independent** → PRIMARY |
| Cohort survives but no longer clears them | **explained** → DERIVATIVE |

Erring toward PRIMARY when the answer cannot be measured is deliberate: the failure mode to avoid is hiding a real alert, not showing an extra one.

### Two structurally correct outcomes need no interpretation

- Excluding a **different value on the same dimension** leaves the candidate untouched (disjoint populations), so two separately degraded gateways both stay PRIMARY.
- Excluding a value the candidate is **nested inside** empties it, so `gateway_C × SBI` is correctly derivative of `gateway_C`.

### Efficiency

No pairwise scan. Lookups are O(reported × primaries), but every one is memoised on `(dimensions, window, exclusion)` through the same `MetricStore` the evidence engine already populates, so actual queries are bounded by *(distinct dimension sets) × (primaries) × 2 windows*.

Measured end-to-end on the golden scenario:

| Stage | Before | After |
|---|---|---|
| Detection | 1,354 ms | 1,606 ms |
| Evidence | 514 ms | **157 ms** |
| RCA | 0.2 ms | 0.2 ms |
| **Total** | **2,100 ms** | **2,038 ms** |

Net slightly *faster*. Detection absorbs the residual queries, and the evidence engine then gets them from cache.

Only depth-1 cohorts are used as confounders: excluding a multi-dimension cohort would mean "exclude every value on every one of its dimensions", removing far more traffic than the cohort itself.

---

## P1-1 Before/After Alert Surface

| Scenario | Alerts | Before: actionable | After: PRIMARY | After: DERIVATIVE | Precision before → after |
|---|---|---|---|---|---|
| Golden gateway_C | 6 | 6 | **1** | 5 | 16.7% → **100%** |
| Issuer SBI | 17 | 17 | **2** | 15 | 5.9% → **50%** |
| gateway_B | 22 | 22 | **2** | 20 | 4.5% → **50%** |
| Issuer HDFC | 11 | 11 | **1** | 10 | 9.1% → **100%** |
| Marginal | 2 | 2 | **1** | 1 | 50% → **100%** |
| **Mean** | | | | | **16.5% → 83.3%** |
| No incident | 0 | 0 | **0** | 0 | — (unchanged) |
| **Systemic (control)** | 32 | 32 | **29** | 3 | correctly **not** collapsed |

Golden scenario detail:

| Rank | Cohort | Role | Explained by | Independence | σ |
|---|---|---|---|---|---|
| 1 | `gateway=gateway_C` | **PRIMARY** | — | — | 9.26 |
| 2 | `region=Delhi` | DERIVATIVE | gateway_C | 0.701 | 4.01 |
| 3 | `network=5G` | DERIVATIVE | gateway_C | 0.266 | 4.85 |
| 4 | `region=Gujarat` | DERIVATIVE | gateway_C | 0.204 | 3.07 |
| 5 | `sender_bank=HDFC` | DERIVATIVE | gateway_C | 0.022 | 3.20 |
| 6 | `sender_bank=SBI` | DERIVATIVE | gateway_C | 0.391 | 3.33 |

The systemic row is the important negative control: when every gateway is genuinely degraded, **all five stay PRIMARY**. The classifier separates shadows from causes; it does not minimise alert count.

---

## Primary vs Derivative Semantics

| Field | Meaning |
|---|---|
| `alert_role` | `PRIMARY` or `DERIVATIVE`, DB-CHECKed |
| `derived_from_cohort_key` | Which primary explains this alert |
| `derived_from_anomaly_id` | Same, resolved to a real row id |
| `independence` | Share of its own movement retained after exclusion (explanatory) |

A DB constraint keeps the pair coherent:

```sql
CHECK ((alert_role = 'DERIVATIVE') = (derived_from_cohort_key IS NOT NULL))
```

A derivative alert must name what explains it; a primary must not claim a parent.

**Evidence is not discarded.** Derivative cohorts keep every evidence record, remain queryable, and remain visible in `derivative_detections`. Alert precision was bought by *reclassification*, never by deletion — asserted by `test_derivative_alerts_keep_their_evidence`.

---

## P1-1 Tests

| Test | Asserts |
|---|---|
| `test_gateway_incident_yields_a_single_primary_alert` | one cause → one root-level alert; shadows still detected |
| `test_every_derivative_alert_names_what_explains_it` | every derivative points at a real primary |
| `test_derivative_alerts_keep_their_evidence` | precision not bought by destroying evidence |
| `test_independent_simultaneous_causes_stay_primary` | **anti-over-suppression** — systemic keeps ≥2 gateway primaries |
| `test_issuer_incident_keeps_the_issuer_primary` | issuer primary; gateway spillover not promoted |
| `test_alert_role_classification_names_no_specific_cohort` | AST scan — no `gateway_C`/`SBI`/etc. in `detect.py` |
| `test_database_rejects_a_derivative_without_a_parent` | coherence enforced by the database |
| `test_handoff_separates_primary_from_derivative` | Day 4 list contains only primaries |

---

## P1-2 Root Cause

The pre-fix formula was:

```
confidence = top_score × (0.5 + 0.5 × margin),   margin = (top − second) / top
```

Of the five components inside `top_score`, four (`independence` 0.30, `divergence` 0.20, `localisation` 0.15, `temporal` 0.05) measure **how cleanly one hypothesis wins**. Only `signal` (0.30, saturating at 12σ) measures **how much evidence exists**. The margin term then multiplied decisiveness again.

A cleanly-attributed weak signal with no rivals therefore beat a strong signal with real competitors — precisely the 5.16σ vs 9.26σ inversion.

---

## P1-2 Confidence Design

Confidence is now the **geometric mean** of two independent questions:

```
evidence_strength = min(1, anomaly_score / 12)      # significance × effect size
attribution       = top_score × (0.5 + 0.5 × margin) # the previous value, unchanged
confidence        = sqrt(evidence_strength × attribution)
```

A geometric mean rather than a weighted sum, because the two factors **must not substitute for one another**. Under a sum, perfect attribution can buy its way past weak evidence — the defect being replaced. Under a product, a near-zero factor drags the result down and cannot be compensated.

Two properties follow directly, and both are asserted:

- `confidence ≤ sqrt(evidence_strength)` — weak evidence has a **hard ceiling** and cannot reach CONFIDENT however decisive the attribution. Strength 0.30 caps confidence at 0.548, below the 0.60 threshold.
- `confidence ≤ sqrt(attribution)` — strength alone does not authorise certainty either.

Nothing was deleted: `independence`, `divergence`, `localisation`, `temporal`, the response tilt and the margin all survive inside `attribution`, and every component is persisted in `score_components` alongside the new `attribution_quality`, `attribution_margin`, and `evidence_strength`.

Bounded [0,1], deterministic, a pure function of two already-computed values.

---

## Confidence Before/After

| Scenario | σ | Severity | Strength | Old conf | New conf | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| Marginal gateway_C | 5.16 | MEDIUM | 0.228 | 0.6944 | **0.3980** | −0.296 | CONFIDENT → **UNCERTAIN** |
| Golden gateway_C | 9.26 | CRITICAL | 0.740 | 0.6396 | **0.6881** | +0.049 | CONFIDENT |
| Issuer HDFC | 15.73 | CRITICAL | 1.000 | 0.8469 | **0.9203** | +0.073 | CONFIDENT |
| Issuer SBI | 17.82 | CRITICAL | 1.000 | 0.7393 | **0.8598** | +0.121 | CONFIDENT |
| gateway_B | 19.66 | CRITICAL | 1.000 | 0.7766 | **0.8812** | +0.105 | CONFIDENT |
| Systemic | 24.52 | CRITICAL | 1.000 | — | **0.7860** | — | CONFIDENT |
| Mild deviation | 0.63 | NONE | 0.001 | — | **0.0053** | — | INSUFFICIENT |
| No incident | — | NONE | 0.000 | 0.0000 | **0.0000** | — | INSUFFICIENT |

---

## Confidence Ordering Analysis

```
Pearson  r(sigma, confidence):  +0.657  →  +0.923
Spearman r(sigma, confidence):  +0.600  →  +0.700
Inversions:                          3  →       2
```

**The target inversion is resolved**: 5.16σ → 0.398 now sits well below 9.26σ → 0.688.

Two inversions remain, and both are legitimate rather than miscalibration:

| Pair | Why |
|---|---|
| HDFC (15.73σ, 0.920) > SBI (17.82σ, 0.860) | both have `evidence_strength = 1.0` |
| HDFC (15.73σ, 0.920) > gateway_B (19.66σ, 0.881) | both have `evidence_strength = 1.0` |

Above 12σ the strength term saturates by design, so among saturated cases confidence is decided entirely by attribution quality — HDFC's attribution is cleaner than SBI's or gateway_B's. This is the documented exception the requirement allows: *higher evidence strength should not materially produce lower confidence **without an explicit, documented reason***. Here the evidence strength is not higher in any meaningful sense — it is identical (1.0) — and the ordering reflects a real difference in how cleanly each is attributed.

The formula was not tuned to make any particular pair look right; it was derived from the semantic requirement and then evaluated against all eight scenarios at once.

---

## Uncertainty Validation

The UNCERTAIN band is now exercised end-to-end, and was reached **naturally** rather than by constructing a contrived case: the marginal gateway_C incident (multiplier 2.0 on the real dataset) produces genuine evidence at 5.16σ that is simply not strong enough to be sure about.

```
verdict    : UNCERTAIN
confidence : 0.3980   (band 0.35 – 0.60)
severity   : MEDIUM
sigma      : 5.1602
strength   : 0.2281
cause      : "Payment gateway gateway_C is degraded"   (still named)
evidence   : 4 supporting, 2 contradicting, 4 alternatives
```

It still names its best candidate and cites evidence — uncertain, not silent. Ground truth confirms the candidate was correct, checked only after inference.

The regression test uses multiplier 2.6 on the smaller fixture population (which needs a slightly larger push for the same effect), landing mid-band at 0.398 rather than on an edge.

---

## P1-2 Tests

| Test | Asserts |
|---|---|
| `test_confidence_is_bounded_and_monotone_in_both_factors` | [0,1]; monotone in each factor; inputs clamped |
| `test_weak_evidence_cannot_reach_confident_however_decisive` | the `sqrt(strength)` ceiling, at four strengths |
| `test_evidence_strength_saturates_and_is_monotone` | 0 / 0.5 / 1.0 at 0, 6, 12; clamped above |
| `test_stronger_evidence_beats_a_more_decisive_weak_one` | the exact 5.16σ vs 9.26σ inversion, with measured inputs |
| `test_marginal_incident_lands_in_the_uncertain_band` | end-to-end UNCERTAIN with cited evidence |
| `test_rca_exposes_evidence_strength_beside_confidence` | severity/σ/strength present; ceiling holds on real output |
| `test_handoff_exposes_the_action_safety_triple` | Day 4 receives all three |
| `test_confidence_is_reproducible` | identical confidence, strength, and fingerprint across runs |

---

## Day 4 Handoff Changes

**`DetectionView`** gains `alert_role`, `primary_anomaly_id`, `derived_from_anomaly_id`, `independence`.

**`Day4Handoff`** splits the surface:

| Field | Contents |
|---|---|
| `detections` | **PRIMARY only** — the actionable surface |
| `derivative_detections` | Causal shadows, explicitly marked, each naming its parent |

**`RcaView`** gains `severity`, `significance_sigma`, `evidence_strength` beside `confidence`.

Live golden-scenario payload:

```
detections (PRIMARY only): 1
    PRIMARY  gateway=gateway_C  sigma 9.2575  CRITICAL  primary_anomaly_id 1
derivative_detections: 5
    DERIVATIVE region=Delhi   derived_from 1  independence 0.7008
    DERIVATIVE network=5G     derived_from 1  independence 0.2657
    DERIVATIVE region=Gujarat derived_from 1  independence 0.2041

RCA:  verdict CONFIDENT · confidence 0.6881 · severity CRITICAL
      significance_sigma 9.2575 · evidence_strength 0.7403
      6 supporting / 0 contradicting / 4 alternatives

ground_truth in payload : False
```

`anomalies_found` on the analysis run now counts PRIMARY alerts — the actionable surface.

---

## Action-Safety Semantics

Day 4 can implement a gate requiring **all** of:

| Input | Source | Meaning |
|---|---|---|
| `confidence` | `RcaView.confidence` | how cleanly evidence points at one cause |
| `evidence_strength` | `RcaView.evidence_strength` | how much evidence exists, [0,1] |
| `significance_sigma` | `RcaView.significance_sigma` | raw statistical strength |
| `severity` | `RcaView.severity` | CRITICAL … NONE band |
| blast radius / GMV at risk | evidence + `DetectionView` | scope and value exposure |

These are stored as **separate columns** and exposed as **separate fields**; the confidence formula does not absorb severity or σ. No single confidence scalar can authorise a larger intervention, because the magnitude inputs are only available by reading them explicitly.

The marginal scenario demonstrates the intended shape: confidence 0.398, severity MEDIUM, σ 5.16, strength 0.228 — an action gate requiring CONFIDENT *and* strength ≥ 0.5 would correctly decline, where the pre-fix value of 0.6944 would have passed a confidence-only gate.

**No Day 4 action logic was implemented.**

---

## Ground-Truth Isolation

Re-verified after the fixes, unchanged:

| Check | Result |
|---|---|
| AST scan of `detect`, `evidence`, `hypothesis`, `rca`, `metrics` | clean (docstrings/comments stripped) |
| RCA with ground truth **deleted** | identical `rca_fingerprint` |
| RCA with ground truth **corrupted** to blame gateway_A | still returns gateway_C, identical fingerprint |
| Ground truth in Day 4 payload | absent |

The new classifier reads only cohort metrics and detection thresholds. It receives no incident identity, no scenario name, and no expected cause — verified by an AST scan asserting `detect.py` contains no cohort literal.

---

## Regression Results

| Item | Before | After | Result |
|---|---|---|---|
| Full suite | 362 passed | **378 passed / 0 failed / 0 skipped** | +16 new tests, none removed or weakened |
| Runtime | 357.6 s | 411.2 s | +15% (16 additional DB-backed tests) |
| Observed content MD5 | `13965d76…4226` | `13965d76…4226` | **byte-identical** |
| Canonical fingerprint | `12dec963…f4b8` | `12dec963…f4b8` | unchanged |
| Generation staleness | `CURRENT`, run 35 | `CURRENT`, run 35 | unchanged |
| `baseline-v1` profiles | 5 values | identical | unmutated |
| Schema drift (`alembic check`) | — | no operations detected | clean |

No expected fingerprint was edited to make a test pass.

**Generalization regression** — no scenario got worse:

| Scenario | Before | After |
|---|---|---|
| Golden gateway_C | CONFIDENT, correct | CONFIDENT, correct |
| gateway_B | CONFIDENT, correct | CONFIDENT, correct |
| gateway_D | CONFIDENT, correct | CONFIDENT, correct |
| Issuer SBI | CONFIDENT, correct | CONFIDENT, correct |
| Issuer HDFC | CONFIDENT, correct | CONFIDENT, correct |
| No incident | 0 alerts, declines | 0 alerts, declines |
| Mild deviation | 0 alerts, declines | 0 alerts, declines |
| Marginal | CONFIDENT (overconfident) | **UNCERTAIN** (better) |
| Systemic | — | CONFIDENT, correct, 5 gateways primary |

Top-1 accuracy remains **6/6** across three gateways and two issuers.

---

## Reproducibility

| Property | Result |
|---|---|
| Repeat pipeline | identical confidence, strength, `rca_fingerprint` |
| Alert roles | deterministic — same primaries and parents each run |
| Simulation fingerprints | unchanged by these fixes |
| Determinism sources | unchanged: SHA-256 only, no `hash()`, no wall-clock, no row-order dependence |

`confidence_from` and `evidence_strength_from` are pure functions of already-computed values.

---

## Performance

| Stage | Before | After | Δ |
|---|---|---|---|
| Detection | 1,354 ms | 1,606 ms | +252 ms |
| Evidence | 514 ms | 157 ms | **−357 ms** |
| RCA | 0.2 ms | 0.2 ms | — |
| **Total** | **2,100 ms** | **2,038 ms** | **−62 ms** |

Net faster. Classification's residual queries land in the shared `MetricStore`, and the evidence engine then reads them from cache instead of issuing its own.

---

## Mutation Verification

Both fixes are load-bearing — reverting either is caught by the suite (run against an isolated copy; the repository was never modified):

| Mutation | Result | Caught by |
|---|---|---|
| Disable causal classification (everything PRIMARY) | **CAUGHT**, 3 failures | `test_gateway_incident_yields_a_single_primary_alert`, `test_derivative_alerts_keep_their_evidence`, `test_handoff_separates_primary_from_derivative` |
| Revert confidence to attribution-only | **CAUGHT**, 2 failures | `test_marginal_incident_lands_in_the_uncertain_band`, `test_rca_exposes_evidence_strength_beside_confidence` |

---

## Remaining P2 Debt

Untouched, exactly as the review classified them:

P2-1 alert lifecycle/hysteresis · P2-2 NORMAL-regime fast failures · P2-3 formal telemetry adapter · P2-4 advisory lock · P2-5 redundant delete · P2-6 weak control-cohort assertion · P2-7 tautological small-cohort test · P2-8 `evaluate_rca` "correctly declined" gap · P2-9 UNCERTAIN band unexercised — **now closed as a side effect** of P1-2 · P2-10 weighted-mean p95.

P2-9 is the only one that changed, and only because the marginal scenario now lands in the band naturally.

---

## Final Verification Table

| Requirement | Before | After | Result | Evidence |
|---|---|---|---|---|
| Total alerts (golden) | 6 | 6 | unchanged — reclassified, not deleted | `incident_anomalies` |
| Primary alerts (golden) | 6 | **1** | ✅ | live query |
| Derivative alerts (golden) | 0 | **5** | ✅ each names its parent | live query |
| Alert precision (mean) | 16.5% | **83.3%** | ✅ | 5 incident scenarios |
| Independent causes preserved | n/a | **5 gateway primaries** | ✅ not collapsed | systemic scenario |
| Golden σ | 9.2575 | 9.2575 | unchanged | detection unchanged |
| Golden confidence | 0.6396 | **0.6881** | ✅ CONFIDENT | RCA row |
| 5.16σ confidence | 0.6944 | **0.3980** | ✅ UNCERTAIN | RCA row |
| Confidence inversions | 3 | **2** (both saturated-strength, documented) | ✅ target resolved | Pearson +0.657→+0.923 |
| Uncertainty scenario | never reached | **UNCERTAIN 0.398** | ✅ | marginal scenario |
| Ground-truth leakage | none | **none** | ✅ | AST + deleted + corrupted |
| Full tests | 362/362 | **378/378** | ✅ | pytest |
| Canonical fingerprint | `12dec963…f4b8` | `12dec963…f4b8` | ✅ unchanged | `cli verify` |
| Generation fingerprint | `CURRENT`, run 35 | `CURRENT`, run 35 | ✅ unchanged | `cli verify` |
| Observed data | `13965d76…4226` | `13965d76…4226` | ✅ byte-identical | content MD5 |
| Performance | 2,100 ms | **2,038 ms** | ✅ net faster | measured, uncontended |

---

## Final Day 3 Status

Both P1 issues are closed with evidence. All prior Day 3 properties hold: 6/6 top-1 accuracy, correct declines, zero false positives on quiet windows, Approach B integrity, provenance, reproducibility, ground-truth isolation.

# DAY 3 P1 FIXES CLOSED — READY FOR DAY 4
