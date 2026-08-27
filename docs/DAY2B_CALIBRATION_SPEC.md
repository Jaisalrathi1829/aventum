_Aventum internal design document — synthetic model calibration._

# Day 2B Calibration Specification

How measurements from the Nigerian Card Payment Dataset for Predictive Routing were transferred into Aventum's synthetic infrastructure model — and, just as importantly, what was deliberately **not** transferred.

---

## The boundary this document exists to enforce

The Nigerian routing dataset is an **independent, itself-synthetic calibration reference** ([ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §4), classified **B — simulation/calibration data** by [ROUTING_DATASET_DECISION.md](ROUTING_DATASET_DECISION.md). It is:

- **not** production telemetry;
- **not** joined to any UPI transaction (no join key exists on any dimension — [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §6);
- **not** evidence about any UPI transaction.

No row of it is imported into the database. Verified by test (`test_calibration_reference_rows_are_never_imported`): the reference appears in Aventum only as a *name* recorded on synthetic configuration rows, alongside which rail informed each gateway's relative profile.

Reference measurements and derived Aventum parameters live in separate namespaces in `backend/aventum_synth/calibration.py` (`REFERENCE_*` vs everything else) so a reader can always tell which is which.

---

## Transfer taxonomy

| Type | Meaning |
|---|---|
| **Direct transfer** | Reference value adopted with only formatting changes. |
| **Scaled transfer** | Reference *structure* retained; absolute values re-chosen for Aventum. |
| **Bounded transfer** | Reference *relationship* retained but deliberately compressed or clamped. |
| **Conceptual template** | Only the *idea* is borrowed; the numbers are Aventum's own. |
| **Not transferred** | Measured but deliberately unused, with a reason. |

---

## Parameter-by-parameter transfer

### 1. Inter-gateway failure-rate spread → `relative_failure_multiplier`

| | |
|---|---|
| **Calibration measurement** | Per-rail failure rate 1.85% (rail_A) → 3.83% (rail_C), a **2.07× ratio**; traffic-weighted mean 2.68% ([ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §12). |
| **Interpretation** | In its own normal operation, the reference ecosystem sustained a roughly 2× spread between its best and worst rail. The *ordering and relative structure* are informative; the *magnitude* is a property of Nigerian card rails, not of Indian UPI. |
| **Aventum parameter** | Unitless multiplier against the fleet mean: A 0.814, D 0.935, B 1.038, E 1.161, C 1.257 (`derive_relative_failure_multipliers()`). |
| **Transformation** | Ratio-to-mean, then damped toward 1.0 with λ = `FAILURE_SPREAD_DAMPING` = 0.6. |
| **Type** | **Bounded transfer** |
| **Reason** | Two reasons for damping. (a) A 2× baseline spread risks *reading as* an incident before any incident exists, violating the "baseline must represent normal operation" requirement. (b) It would leave a later injected degradation little headroom to stand out. Damping compresses the spread to **1.54×**, which is differentiated but unambiguously normal. Asserted by `test_calibrated_failure_spread_is_differentiated_but_not_an_incident` (band 1.2–1.8). |

### 2. Absolute failure level → `baseline_failure_probability`

| | |
|---|---|
| **Calibration measurement** | Reference overall failure rate 2.68%. |
| **Interpretation** | Not applicable to UPI. The canonical dataset has its own observed failure rate. |
| **Aventum parameter** | Per-gateway absolute probabilities: A 4.02%, D 4.62%, B 5.13%, E 5.73%, C 6.21%. |
| **Transformation** | Relative multipliers × a scale factor chosen so the traffic-weighted mean equals the **observed** canonical failure rate (4.9504%), read from `transactions` at generation time. |
| **Type** | **Not transferred** (the level is observed, not calibrated) |
| **Reason** | The calibration reference must never set the *level* of anything — only the *shape*. Anchoring to observed data means attaching synthetic gateways cannot distort the observed aggregate. Asserted by `test_absolute_failure_probabilities_preserve_the_observed_rate`. |

### 3. Traffic distribution → `baseline_traffic_weight`

| | |
|---|---|
| **Calibration measurement** | Rail shares 27.8% / 26.4% / 20.9% / 12.8% / 12.1%. |
| **Interpretation** | Real gateway fleets carry uneven but substantial traffic — no rail is vestigial, none dominates completely. |
| **Aventum parameter** | B 0.27, A 0.26, D 0.21, C 0.13, E 0.13. |
| **Transformation** | Rounded to clean values so the configuration is legible; normalised to sum to 1. |
| **Type** | **Conceptual template** |
| **Reason** | The precise shares carry no meaning outside their own ecosystem. What matters is the *property* — five gateways, all with enough volume for later per-gateway analysis. Confirmed: the smallest gateway still carries 32,202 transactions (§Demo readiness). |

### 4. Latency regime structure → `LATENCY_REGIME_PARAMS`

| | |
|---|---|
| **Calibration measurement** | Three clean, separated regimes: approved ≈500 ms (σ≈120); non-timeout failures ≈899 ms (σ≈250, ~1.8×); timeouts ≈3,986 ms (σ≈799, ~8×) with a sharp 2,000 ms floor. |
| **Interpretation** | The **three-tier structure** and the **approximate ratios between tiers** are the transferable insight. The absolute milliseconds are a property of the reference environment. |
| **Aventum parameter** | Lognormal per regime: NORMAL median 420 ms (σ 0.32, band 40–1,800); ELEVATED median 860 ms (σ 0.30, band 180–1,990); TIMEOUT median 3,400 ms (σ 0.24, band 2,000–8,000). |
| **Transformation** | Structure and inter-tier ratios retained (~2.0× and ~8.1×, close to the reference's 1.8× and 8.0×); absolute medians chosen for Aventum; each regime hard-clamped to its band. |
| **Type** | **Scaled transfer** |
| **Reason** | No individual reference latency value is copied. Lognormal (rather than normal) gives the right-skew real latency exhibits — something the reference's mean/σ summary alone would not reproduce. The hard clamps are what make it structurally impossible for a NORMAL draw to wander into timeout territory (`test_extreme_uniforms_stay_inside_the_regime_band`). |

### 5. Per-gateway latency offsets → `GATEWAY_LATENCY_MULTIPLIER`

| | |
|---|---|
| **Calibration measurement** | Reference rails differed by only 3.18 ms at p50 — essentially undifferentiated in latency, despite differing ~2× in failure rate. |
| **Interpretation** | Latency and failure rate are largely **independent** axes of gateway character in the reference. Making them move together would be an unsupported invention. |
| **Aventum parameter** | A 0.96, D 0.99, B 1.00, E 1.05, C 1.08. |
| **Transformation** | Small multiplicative offsets, ordered consistently with the failure profile but far weaker. |
| **Type** | **Bounded transfer** |
| **Reason** | A deliberate, documented departure: the reference showed *no* meaningful latency spread, but a completely flat latency profile would give a later RCA component nothing to discriminate on. The offsets are kept small (≤8%) so latency remains a weak baseline signal that a real degradation can later dominate. |

### 6. Response taxonomy → `RESPONSE_TAXONOMY`

| | |
|---|---|
| **Calibration measurement** | Six response values: Approved plus five failure families (Insufficient Funds, Processing Error, Issuer Declined, Do Not Honor, Timeout). |
| **Interpretation** | A realistic decline taxonomy separates *issuer-side* refusals from *infrastructure-side* faults — the distinction that later makes RCA possible at all. |
| **Aventum parameter** | `APPROVED`, `INSUFFICIENT_FUNDS`, `ISSUER_DECLINED`, `PROCESSING_ERROR`, `DO_NOT_HONOR`, `TIMEOUT`, each mapped to an attribution of `approved` / `issuer_side` / `infrastructure_side`. |
| **Transformation** | Re-cased to Aventum's own `UPPER_SNAKE` convention; attribution mapping added. |
| **Type** | **Direct transfer** (vocabulary) + **conceptual template** (attribution) |
| **Reason** | The vocabulary is a plausible generic decline taxonomy. **These are explicitly not real Razorpay, UPI, or NPCI production error codes** — the naming convention is deliberately Aventum-specific to prevent that misreading. The attribution mapping is Aventum's own addition, not a reference measurement. |

### 7. Failure-response mix → `BASELINE_FAILURE_RESPONSE_MIX`

| | |
|---|---|
| **Calibration measurement** | Among failures: Insufficient Funds 24.5%, Processing Error 24.4%, Issuer Declined 24.2%, Do Not Honor 23.9%, Timeout 2.95%. |
| **Interpretation** | Roughly even four-way decline split with a small timeout tail. |
| **Aventum parameter** | Per-gateway mixes centred on that split, tilted so better-profiled gateways carry more issuer-side attribution and worse-profiled gateways more infrastructure-side (gateway_A: 20.6% PROCESSING_ERROR / 1.8% TIMEOUT → gateway_C: 27.1% / 3.8%). |
| **Transformation** | Near-direct on the base split; the per-gateway tilt is added. |
| **Type** | **Direct transfer** + **Aventum modelling decision** (the tilt) |
| **Reason** | The tilt is what makes the response distribution *diagnostic* rather than decorative: a gateway with infrastructure problems should fail **differently**, not merely more often. The reference does not measure this, so it is flagged as an Aventum decision. It is also the hook Day 2C uses — `timeout_multiplier` on a health window shifts the mix toward infrastructure-side families automatically (`test_health_degradation_shifts_the_response_mix_toward_infrastructure`). |

### 8. Isolated rail degradation pattern → *not implemented in Day 2B*

| | |
|---|---|
| **Calibration measurement** | Each rail showed 3–5 discrete hours (of 168) at 12–26% failure — 8–15× its own baseline — while every other rail stayed normal ([ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §9). |
| **Interpretation** | The best available evidence for what a realistic, isolated, time-bounded gateway degradation looks like in magnitude, duration, and *non-contagion*. |
| **Aventum parameter** | None in Day 2B. |
| **Type** | **Not transferred (deferred)** |
| **Reason** | Day 2B's scope boundary forbids injecting degradation; the baseline must represent normal operation. The schema is built to accept it without change: `synthetic_gateway_health_states` takes time-bounded windows with failure/latency/timeout multipliers, and this measurement gives Day 2C its magnitude (8–15×), duration (~1 hour in the reference), and isolation parameters. |

---

## Summary of transfer types

| Type | Parameters |
|---|---|
| Direct transfer | Response taxonomy; base failure-response split |
| Scaled transfer | Latency regime medians and σ |
| Bounded transfer | Inter-gateway failure spread (λ=0.6); per-gateway latency offsets |
| Conceptual template | Traffic weights; response attribution mapping |
| Not transferred | Absolute failure level (observed instead); isolated degradation pattern (deferred to Day 2C) |

## What is recorded at runtime

Every generation run persists the full parameter set to `synthetic_generation_runs.model_parameters`, including `calibration_reference` (name, version, and the "not production telemetry" note) and `failure_spread_damping`. Every gateway row records `calibration_source_rail` and `calibration_reference_name`; every profile records the damping and the anchoring note in `calibration_notes`. Calibration provenance is therefore queryable, not just documented — asserted by `test_gateways_record_calibration_provenance`.
