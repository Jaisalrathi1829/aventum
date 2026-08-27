_Aventum internal design document — readiness analysis, not an implementation._

# Day 2B Flagship Demo Readiness

Can the synthetic baseline support the intended flagship incident scenario later? This is a **readiness check only** — no incident is injected and no anomaly detection is built.

---

## Why this needs measuring, not assuming

Two independently-true facts pull in opposite directions:

- **Aggregate cohort volumes are large.** `gateway_B × SBI × P2P` holds 7,467 transactions.
- **Temporal density is thin.** Day 1 measured ~687 transactions/day across the *entire* dataset ([AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §9), with 12.46% of 10-minute windows empty.

A cohort can therefore look comfortably large in total and still be far too thin inside an incident window. Detectability depends on **transactions per window**, not transactions overall — so that is what is measured below.

---

## Baseline gateway volumes (250,000 transactions)

| Gateway | Total | Share | Baseline failure rate | Per day (avg) | Per day (min–max) |
|---|---|---|---|---|---|
| gateway_B | 67,365 | 26.95% | 5.157% | 184.1 | 9 – 243 |
| gateway_A | 65,145 | 26.06% | 3.937% | 178.0 | 13 – 222 |
| gateway_D | 52,597 | 21.04% | 4.677% | 143.7 | 7 – 190 |
| gateway_C | 32,691 | 13.08% | 6.421% | 89.3 | 2 – 114 |
| gateway_E | 32,202 | 12.88% | 5.521% | 88.0 | 5 – 118 |

Even the smallest gateway carries 32,202 transactions — asserted by `test_full_scale_flagship_cohort_has_usable_volume`.

## Top 3-way cohorts (gateway × sender_bank × payment_method)

| Cohort | Volume | Baseline failure rate |
|---|---|---|
| gateway_B × SBI × P2P | 7,467 | 4.942% |
| gateway_A × SBI × P2P | 7,445 | 4.164% |
| gateway_B × SBI × P2M | 6,051 | 5.288% |
| gateway_D × SBI × P2P | 5,809 | 5.027% |
| gateway_A × SBI × P2M | 5,690 | 3.919% |

2-way `gateway_B × SBI` holds 16,893 (5.079% baseline); `gateway_C × SBI` holds 8,185 (6.268%).

---

## Detectability: how large a degradation must be to show

Failure rate required for a **3σ** signal against the cohort's own baseline, by cohort and incident-window length (binomial standard error, `3σ = p + 3·√(p(1−p)/n)`):

| Cohort | txns/day | Baseline | 1-day window | 3-day window | 7-day window |
|---|---|---|---|---|---|
| gateway_B (all traffic) | 184.1 | 5.16% | **10.0%** | **8.0%** | **7.0%** |
| gateway_C (all traffic) | 89.3 | 6.42% | 14.2% | 10.9% | 9.4% |
| gateway_B × SBI | 46.2 | 5.08% | 14.8% | 10.7% | 8.7% |
| gateway_B × SBI × P2P | 20.5 | 4.94% | 19.3% | 13.2% | 10.4% |

Reading this honestly:

- **Gateway-level, multi-day windows are comfortable.** A degradation of gateway_B from 5.2% → 15% over 3 days is ~11σ — overwhelming.
- **3-way cohorts need either a long window or a large effect.** At ~20 txns/day, a 1-day incident needs the rate to nearly quadruple before it clears 3σ.
- **Sub-daily windows are not viable at any cohort depth.** At 184 txns/day, gateway_B sees ~7.7/hour; a 1-hour incident cohort is single-digit transactions. This is the Day 1 finding resurfacing, not a Day 2B limitation.

---

## Recommended flagship cohort

### Primary: `gateway_C`, multi-day window

| Property | Value |
|---|---|
| Baseline volume | 32,691 transactions (13.08% of traffic) |
| Baseline failure rate | 6.421% |
| Per-day volume | 89.3 |
| Suggested incident window | 3 days (~268 transactions) |
| Suggested degraded rate | 20–25% |
| Expected signal | ~54–67 failures vs ~17 expected — **≈9–13σ** |

**Why gateway_C:** it already carries the highest calibrated baseline (6.42%), so a degradation reads as a *worsening of a known-weaker gateway* rather than an implausible reversal — the more realistic incident narrative. At 13% of traffic it is significant without dominating, so the four healthy gateways form a strong control group. Its response mix is already tilted toward infrastructure-side attribution (27.1% `PROCESSING_ERROR`, 3.8% `TIMEOUT`), giving Day 2C a natural axis to amplify.

### Secondary: `gateway_B × SBI`, 3–7 day window

16,893 transactions, 46.2/day, 5.079% baseline. Demonstrates a **segment-scoped** incident (one gateway *and* one issuer) rather than a whole-gateway one — a harder and more interesting RCA problem, since the diagnosis must isolate an intersection. Needs ≥3 days and a degradation to ~11%+.

### Not recommended

- **Sub-daily incident windows.** Insufficient density at any cohort depth.
- **3-way cohorts with short windows.** `gateway_B × SBI × P2P` at 20.5/day needs a 7-day window even for a 2× degradation.
- **gateway_A as the incident subject.** It has the *lowest* baseline (3.94%); degrading the healthiest gateway is a less natural narrative, though it would be statistically easier to spot.

---

## What Day 2C must supply

The baseline is ready; the incident layer needs to add:

1. A `DEGRADED` window in `synthetic_gateway_health_states` for the chosen gateway and period — the schema accepts this **without migration**, which is why health was modelled as a time-bounded interval with multipliers rather than a column.
2. Multiplier values consistent with the calibration evidence — the reference measured isolated rail degradations at **8–15× baseline** ([DAY2B_CALIBRATION_SPEC.md](DAY2B_CALIBRATION_SPEC.md) §8).
3. Incident ground-truth records, kept strictly out of the diagnosis path ([DAY2B_TRUTH_MODEL.md](DAY2B_TRUTH_MODEL.md)).
4. Regeneration of the affected window, so failure rate, latency, and response mix move **together** through `GatewayRuntimeProfile` rather than being mutated independently.

### One caveat Day 2C must resolve

Under the current status-conditioned assignment ([DAY2B_TRUTH_MODEL.md](DAY2B_TRUTH_MODEL.md)), per-gateway failure rates are constrained by the **observed** canonical outcomes — the total number of failures in any window is fixed by `transactions.status`. Raising gateway_C's failure rate inside a window therefore *redistributes* that window's observed failures toward gateway_C rather than creating new ones.

For the recommended 3-day window this is workable: the window holds ~2,060 transactions with ~102 observed failures, and concentrating a larger share onto gateway_C's ~268 transactions can reach 20–25% without exhausting them. But the ceiling is real and Day 2C must design within it — or, alternatively, decide deliberately (and document) that the incident window generates its own synthetic outcomes rather than reusing observed status. That is a Day 2C architectural decision, flagged here rather than pre-empted.
