# Day 3 P1 Fix Handoff — Alert Roles + Confidence Calibration

Snapshot of the files changed by the **surgical closure of the two Day 3 P1 issues** raised in `docs/DAY3_ARCHITECTURE_REVIEW.md`. Paths mirror the real repo.

All 11 files verified byte-identical to the live repo at copy time.

---

## Status

# DAY 3 P1 FIXES CLOSED — READY FOR DAY 4

**378/378 tests pass.** Observed data, canonical fingerprint, and Day 2 generation fingerprint all byte-identical. No P2 work, no Day 4.

---

## What changed, in one table

| | Before | After |
|---|---|---|
| Golden actionable alerts | 6 | **1 PRIMARY** (+5 derivative, evidence kept) |
| gateway_B actionable alerts | 22 | **2 PRIMARY** (+20 derivative) |
| Mean alert precision | 16.5% | **83.3%** |
| 5.16σ confidence | 0.6944 CONFIDENT | **0.3980 UNCERTAIN** |
| 9.26σ confidence | 0.6396 CONFIDENT | **0.6881 CONFIDENT** |
| σ↔confidence Pearson | +0.657 | **+0.923** |
| Tests | 362 | **378** |
| Golden runtime | 2,100 ms | **2,038 ms** (net faster) |

---

## P1-1 — causal alert roles

Derivative alerts were never a statistics problem: `device=Android` *genuinely* had an elevated failure rate during a gateway incident because it carried that gateway's traffic. No significance threshold removes a real correlation — only a causal test does.

The fix reuses the independence machinery that already existed in `evidence.py`/`hypothesis.py` but had never been applied to the alert surface:

```
candidate → exclude each established PRIMARY → re-run DETECTION's own criteria
   still an anomaly?  yes → PRIMARY   no → DERIVATIVE (records its parent)
```

**The decision test is re-detection, not a ratio.** An earlier iteration thresholded the independence ratio at 0.35 and measurably mis-classified `region=Delhi` as primary — it retained 70% of a movement that would never have alerted on its own. Re-applying the detector's existing thresholds asks the meaningful question and **adds no new tunable constant**.

**Undeterminable ≠ dependent.** A second measured flaw: `device=Android` is 75% of traffic, so excluding it strips other cohorts below the sample-size floor. The first implementation called that dependence and wrongly demoted `gateway_C` and `gateway_E`. The shipped code treats an unmeasurable residual as *not explained*, erring toward keeping an alert visible.

**It does not just minimise alert count.** In a fleet-wide incident all five gateways stay PRIMARY — the negative control that proves the classifier separates shadows from causes rather than collapsing everything.

---

## P1-2 — confidence calibration

Four of the five score components measured *how cleanly a hypothesis wins*; only one measured *how much evidence exists*. So a weak signal with no rivals beat a strong one with real competitors.

```
confidence = sqrt(evidence_strength × attribution)
```

A geometric mean, because the two factors must not substitute for one another. Under a sum, perfect attribution buys its way past weak evidence — the exact defect being replaced.

Two properties follow and are asserted:
- `confidence ≤ sqrt(evidence_strength)` — weak evidence has a **hard ceiling** and cannot reach CONFIDENT however decisive the attribution.
- `confidence ≤ sqrt(attribution)` — strength alone does not authorise certainty either.

Nothing was deleted: independence, divergence, localisation, temporal, response tilt and margin all survive inside `attribution`, and every component is persisted for audit.

**Two inversions remain and are legitimate** — both are between cases whose `evidence_strength` is saturated at 1.0, so confidence is decided entirely by attribution quality. That is the documented exception, not miscalibration.

---

## Action safety

`severity`, `significance_sigma`, and `evidence_strength` are stored as separate columns and exposed as separate handoff fields. The confidence formula does not absorb them, so **no single confidence scalar can authorise a larger intervention**.

The marginal scenario shows the intended shape: confidence 0.398 / severity MEDIUM / σ 5.16 / strength 0.228 — a gate requiring CONFIDENT *and* strength ≥ 0.5 correctly declines, where the pre-fix 0.6944 would have passed a confidence-only gate.

---

## Files

| File | Change |
|---|---|
| `aventum_incident/detect.py` | `_residual_survival`, `_classify_alert_roles`, `primary_alerts`/`derivative_alerts` |
| `aventum_incident/hypothesis.py` | `evidence_strength_from`, `confidence_from`, new confidence assignment |
| `aventum_incident/rca.py` | `severity` · `significance_sigma` · `evidence_strength` on the result |
| `aventum_incident/handoff.py` | `detections` = PRIMARY only; new `derivative_detections`; action-safety triple |
| `aventum_incident/models.py` | alert-role and action-safety columns + CHECK constraints |
| `aventum_incident/pipeline.py` | persists roles, resolves parent ids, counts primaries |
| `aventum_incident/constants.py` | alert-role vocabulary, strength saturation |
| `migrations/versions/0005_…py` | additive migration, 9 columns + 2 CHECKs + 1 index |
| `tests/test_incident_intelligence.py` | +16 tests (95 defs / 118 collected) |

---

## Mutation verification

Both fixes are load-bearing — reverting either is caught (run against an isolated copy; the repo was never modified):

| Mutation | Result |
|---|---|
| Disable causal classification (everything PRIMARY) | **CAUGHT** — 3 failures |
| Revert confidence to attribution-only | **CAUGHT** — 2 failures |

---

## Not touched

All ten P2 items remain open by design. P2-9 (UNCERTAIN band unexercised) closed incidentally — the marginal scenario now lands in the band naturally. No Day 4 work was started.
