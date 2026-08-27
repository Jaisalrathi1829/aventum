_Aventum internal design document — epistemic layering._

# Day 2B Truth Model

Which statements Aventum may make, on what basis, and which layers must never be conflated. This exists because RCA evaluation is only meaningful if "what happened", "what we modelled", and "what the agent concluded" stay separable.

---

## The four layers

| Layer | What it is | Where it lives (today) | May it enter the diagnosis path? |
|---|---|---|---|
| **1. Observed fact** | Recorded in the canonical UPI dataset. Read-only, never regenerated. | `transactions` | Yes — this is the evidence base |
| **2. Synthetic infrastructure state** | Model state Aventum invented: which gateway, its health, its modelled failure probability. | `synthetic_gateways`, `synthetic_gateway_profiles`, `synthetic_gateway_health_states`, `synthetic_infrastructure_assignments` | Yes, but **always labelled synthetic** |
| **3. Synthetic observed signal** | What Aventum would "see" from the infrastructure: latency, response code, attribution. | `synthetic_infrastructure_assignments` | Yes, labelled synthetic |
| **4. Incident ground truth** | The known-by-construction cause of an injected incident. | **Does not exist yet** (Day 2C) | **No — evaluation only** |
| **5. Agent conclusion** | What a later RCA/LLM component infers. | Does not exist yet | It is the *output*, never an input |

Layer 4's exclusion is not a style preference: feeding ground truth into the diagnosis path makes the evaluation circular, which [AVENTUM_DATA_REQUIREMENTS_MATRIX.md](AVENTUM_DATA_REQUIREMENTS_MATRIX.md) §12 rules out.

---

## What is observed, and what is invented

**Observed (Day 2A, read-only):** `transaction_id`, `timestamp`, `amount`, `status`, `payment_method`, `transaction_type`, `merchant_category`, `region`, `device`, `network`, `sender_bank`, `receiver_bank`, `issuer_bank`, `fraud_flag`, `source_dataset`, `ingestion_run_id`.

**Invented (Day 2B, entirely synthetic):** gateway identity, routing policy and decision, gateway health, latency regime, latency value, response code, response attribution, modelled failure probability.

Day 2B writes **nothing** to `transactions` and adds **no** synthetic column to it — enforced by `test_canonical_transactions_are_never_modified`.

---

## The central modelling decision: status-conditioned assignment

This is the one design choice that most needs stating plainly, because a careless reading could mistake it for a causal claim.

### The tension

The forward model Aventum wants is `gateway + context + health → P(failure)`. But `transactions.status` is **observed fact** and read-only. Two bad options present themselves:

- **Generate status from the model.** Rejected: it would overwrite observed outcomes with invented ones, destroying the evidence base.
- **Assign gateways independently of status.** Rejected: every gateway would then show the dataset-average failure rate (~4.95%), the calibrated differentiation would exist only on paper, and per-gateway RCA would have nothing to find.

### What Day 2B actually does

Gateway selection samples from the **posterior** `P(gateway | observed status)` rather than the prior `P(gateway)`:

```
P(g | FAILED)  ∝ w_g × p_g
P(g | SUCCESS) ∝ w_g × (1 − p_g)
```

where `w_g` is the policy traffic weight and `p_g` the calibrated failure probability. This is Bayes applied to the forward model the profiles describe, and it is equivalent to forward-generating outcomes and keeping only draws consistent with the observed status.

### What this does and does not license

**It does:**
- preserve observed marginals **exactly** — every observed failure is assigned to some gateway, none created or destroyed (`test_observed_failure_rate_is_unchanged_by_generation`);
- converge per-gateway failure rate to `p_g` and traffic share to `w_g` (verified at 4σ);
- give a later incident a differentiated backdrop to stand out against.

**It does not:**
- claim any gateway **caused** any particular failure;
- claim the observed failure rate is explained by gateway choice;
- constitute evidence that gateway differentiation exists in real UPI.

**The honest one-line framing:** Aventum *attributes* observed outcomes to synthetic gateways in calibrated proportions. It constructs a plausible infrastructure world consistent with observed data — it does not discover one.

`modeled_failure_probability` is persisted per row precisely so the forward model stays visible and separable: it is the model's belief about a `(gateway, context, health)` combination, independent of what happened to that transaction. Day 2C's counterfactual simulator needs exactly this.

---

## Coherence: what the model may never produce

Fields are generated in a constrained chain, never independently:

```
observed status → response family → latency regime → latency value
```

| Combination | Status | Enforced by |
|---|---|---|
| `SUCCESS` + failure response | Impossible | Generator + `test_success_never_carries_a_failure_response` |
| `FAILED` + `APPROVED` | Impossible | Generator + `test_failure_never_carries_an_approved_response` |
| `TIMEOUT` response + non-`TIMEOUT` regime | Impossible | DB CHECK `ck_synth_assignment_timeout_coherence` |
| `APPROVED` + `issuer_side` attribution | Impossible | DB CHECK `ck_synth_assignment_approved_coherence` |
| `NORMAL` regime above its 1,800 ms cap | Impossible | Clamped in `lognormal_from_uniform`, verified post-load |
| **`APPROVED` + `ELEVATED` latency** | **Permitted** | Deliberate — see below |

The last row was a real correction during implementation. An early constraint forced `APPROVED` into the `NORMAL` regime; the database rejected the first generation because ~4% of successes are drawn slow. The constraint was wrong, not the model: **a slow-but-successful payment is realistic**, and forbidding it would make latency a perfect predictor of outcome — handing a later RCA component a shortcut that would not exist in reality. The genuinely impossible case (`APPROVED` + `TIMEOUT`) remains excluded by the timeout constraint.

---

## Health: model state, not observation

`HEALTHY` / `DEGRADED` / `UNAVAILABLE` are **model states**, not measurements. The intended causal direction is:

```
gateway health state
    → failure probability      (failure_multiplier)
    → latency distribution     (latency_multiplier)
    → response/error mix       (timeout_multiplier)
```

and, in a later phase, the inverse inference:

```
observed rolling metrics → health assessment
```

Health influence is funnelled through a single object (`GatewayRuntimeProfile`) so a Day 2C degradation moves failure rate, latency, **and** response mix together, without mutating each output independently. Verified by `test_health_degradation_raises_modeled_failure_probability` and `test_health_degradation_shifts_the_response_mix_toward_infrastructure`.

Day 2B emits `HEALTHY` for all 250,000 rows — no degradation is injected.

---

## Machine-enforced provenance

Documentation alone is not a boundary. Four mechanisms make the distinction unavoidable:

1. **Table naming.** Every synthetic table is prefixed `synthetic_`. A tool reading `synthetic_infrastructure_assignments` cannot mistake it for payment history the way it might misread a bare `gateways`.
2. **`is_synthetic` with `CHECK (is_synthetic = true)`** on all six synthetic tables — setting it false is rejected by the database, not merely discouraged (`test_is_synthetic_false_is_rejected_by_the_database`, parametrised across all six).
3. **Column naming in the read surface.** `v_transaction_infrastructure` prefixes every column `observed_*` or `synthetic_*` and carries explicit `transaction_provenance = 'OBSERVED'` / `infrastructure_provenance = 'SYNTHETIC'` markers.
4. **Calibration provenance is data.** Each gateway records `calibration_source_rail` and `calibration_reference_name`; each run records the full parameter set and the "not production telemetry" note.

### Answering the review question directly

> *Could a future RCA or Qwen tool accidentally treat synthetic data as observed historical fact?*

Not through the intended read surface. Every synthetic field arrives with a `synthetic_` prefix, from a `synthetic_`-prefixed table, alongside an explicit provenance marker and a database-enforced `is_synthetic` flag. A tool would have to bypass all four mechanisms *and* query the raw tables by name.

The residual risk is a **future tool author** writing a bespoke query that strips the prefixes before handing rows to an LLM. That is a Day 2C+ tool-design obligation, recorded here and in the Day 2B report's Known Limitations rather than claimed as solved.

---

## Statements Aventum may and may not make

**May not** — these would be false:

- "These are real Razorpay gateways / routing decisions / latencies / error codes."
- "These are observed UPI gateway logs."
- "The Nigerian dataset is production telemetry."
- "Gateway C causes more failures" — stated as an observed fact about the real world.
- "This simulated outcome is what actually happened."

**May** — these are supportable:

- "In Aventum's synthetic infrastructure model, gateway_C carries a calibrated baseline failure probability of 6.21%."
- "Modelled latency for this transaction was 421 ms in the NORMAL regime — a simulated infrastructure signal."
- "The gateway failure-rate spread is calibrated against a synthetic reference dataset; it is a modelling parameter, not measured UPI behaviour."
- "This transaction was observed to fail; the synthetic model attributes it to gateway_C with an infrastructure-side response code."

Note the shape of the last one: **observed** outcome, **attributed** infrastructure — the two halves labelled differently in the same sentence. That is the register Aventum's outputs should use throughout.
