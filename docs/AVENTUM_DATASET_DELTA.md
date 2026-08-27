# Aventum Dataset Delta Analysis — What Did the Routing Dataset Actually Add?

Compares the Day 1 data foundation (`upi_transactions_2024` + NPCI reference files) against that same foundation plus the newly audited Nigerian Card Payment Dataset for Predictive Routing. Every classification below is grounded in [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) and [ROUTING_DATASET_SCHEMA_MAPPING.md](ROUTING_DATASET_SCHEMA_MAPPING.md) — not re-derived here. Classification legend: **AVAILABLE BEFORE**, **NEWLY AVAILABLE**, **PARTIALLY IMPROVED**, **ONLY CALIBRATION VALUE**, **STILL SYNTHETIC**, **STILL UNSUPPORTED**.

The single fact that governs every row below: **the new dataset cannot be joined to `upi_transactions_2024` on any key, at any granularity** (confirmed unconditionally in [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §6 — zero shared identifier, zero calendar-date overlap, different currency, no shared bank/merchant vocabulary). Every improvement recorded here is therefore an improvement to **calibration credibility and design evidence**, never an improvement to what data is physically attached to a UPI transaction row.

---

| Requirement | Before new dataset | New dataset contribution | After combining conceptually | Final treatment |
|---|---|---|---|---|
| Transaction identity | `transaction_id`, 100% unique, `upi_transactions_2024` | A second, disjoint identity space (`tx_1`…`tx_2026891`) in an unrelated population | No change to the UPI identity space; the new IDs exist only in their own population | **AVAILABLE BEFORE** |
| Timestamp | Second-precision, 2024 calendar year | Minute-precision, Jan 2025, zero calendar-date overlap with UPI data | No change — the two clocks never touch | **AVAILABLE BEFORE** |
| Amount | INR, 10–42,099 | NGN (presumed), 100–13,305, no FX basis stated | No change — different currency, not comparable | **AVAILABLE BEFORE** |
| Status | Binary SUCCESS/FAILED, 95.05% success | Binary success/failed (lowercase), 97.32% success, disjoint population | Conceptually reinforcing, not additive | **AVAILABLE BEFORE** |
| Payment method | 4-value `transaction type` (P2P/P2M/Bill Payment/Recharge) | `payment_channel` (card/mobile_money/ussd/transfer) — a **different concept** (settlement instrument, not transaction purpose — confirmed in schema mapping) | No legitimate mapping exists; concepts don't align | **AVAILABLE BEFORE** |
| Issuer/bank | 8-bank universe (SBI, HDFC, …) | **No bank field of any kind** in the routing dataset | No contribution | **AVAILABLE BEFORE** |
| Merchant/category | `merchant_category`, valid only for non-P2P rows | `merchant_id` (300 anonymized IDs, genuinely new *concept* but only within its own disjoint population) + `merchant_segment` (different concept — business size, not product category) | `merchant_id` is a new idea worth remembering for a future real card-processing integration, but adds nothing to the UPI-linked merchant dimension today | **AVAILABLE BEFORE** (for the UPI-linked pipeline; `merchant_id` is dataset-local, not transferable) |
| Geography | `sender_state`, 10 Indian states | `region`/`country`, both constant `NG` — zero variation | No contribution (degenerate field) | **AVAILABLE BEFORE** |
| Device/network | `device_type`, `network_type` | **No device or network field of any kind** in the routing dataset | No contribution | **AVAILABLE BEFORE** |
| Gateway identity | **No field, no proxy anywhere in the Day 1 corpus** (score 1/10) | `rail_id` — 5 values, real ~2× failure-rate spread, consistent across payment channels | Cannot attach to any UPI row; supplies a strong, measured design template for the synthetic `gateway_id` | **ONLY CALIBRATION VALUE** |
| Routing path | No field, no proxy | **None** — `rail_id` is a flat categorical outcome, not a path/sequence | No contribution | **STILL UNSUPPORTED** |
| Routing policy | No field, no proxy | **None** — no decision-rule or policy field exists | No contribution | **STILL UNSUPPORTED** |
| Latency | No field, no proxy anywhere (score 1/10) | `latency_ms` — clean 3-tier outcome-dependent shape (500/900/3,986ms) | Cannot attach to any UPI row; supplies a strong, measured design template for synthetic `gateway_latency_ms` | **ONLY CALIBRATION VALUE** |
| Response code | No transaction-grain field (only NPCI's aggregate 2-way proxy) | `gateway_response` — 6-value clean vocabulary with realistic relative frequencies | Cannot attach to any UPI row; upgrades the vocabulary source for synthetic `gateway_response_code` | **ONLY CALIBRATION VALUE** |
| Error code | No transaction-grain field (only NPCI's aggregate 2-way proxy) | `error_code` — same 6-value taxonomy (redundant with `gateway_response`, confirmed 1:1) | Same as response code; confirms one vocabulary is sufficient (no need for two parallel canonical fields) | **ONLY CALIBRATION VALUE** |
| Failure reason | Only NPCI's coarse BD%/TD% 2-way split | 5-way granular reason taxonomy (funds/issuer-generic/issuer/technical/timeout) | Richer vocabulary reference than Day 1 had; still not attachable data | **ONLY CALIBRATION VALUE** |
| Gateway health | No field, no proxy | No explicit health-state field either — only a *derivable* rolling per-rail failure-rate signal within the routing dataset's own population | The synthetic `gateway_health_state` field must still be generated from scratch, but now with a better-evidenced rule of thumb (e.g., "flag degraded when hourly failure rate exceeds ~3× baseline") | **STILL SYNTHETIC** |
| Retry behavior | No field, no proxy | **Confirmed absent** — no retry-count or attempt-sequence field exists; grain is strictly one row per transaction, one attempt, one rail (audit §2) | This audit affirmatively proved the concept is unsupported here too, not merely "not yet found" | **STILL UNSUPPORTED** |
| Incident ground truth | No dataset marks any incident; only one uncontextualized cross-sectional outlier (NPCI, Central Bank of India) | A **measured, isolated, single-rail, ~1-hour, 8–15×-baseline failure spike pattern**, discovered by this audit (not labeled by the source) | Not ground truth for anything (unlabeled, unattachable to UPI) — but the best evidence-based *shape template* Aventum has for its own incident-injection design | **ONLY CALIBRATION VALUE** |
| Anomaly detection | Mechanism works on `upi_transactions_2024`; native failure-rate variance is noise-level (0.2–0.9 pts) | No change to the UPI-linked mechanism; independently proves (in its own population) that a genuinely detectable, non-noise anomaly pattern is achievable when real infrastructure differentiation exists | UPI-linked anomaly detection is exactly as capable as before; confidence that a *synthetically enriched* UPI pipeline can achieve a similarly clean pattern is higher | **ONLY CALIBRATION VALUE** |
| RCA | Mechanism works on 6/9 dimensions; native effect sizes weak | Independently demonstrates gateway-level RCA (`rail_id` → failure rate) working cleanly in its own population | No change to UPI-linked RCA; confidence in the eventual synthetic-gateway RCA design is higher | **ONLY CALIBRATION VALUE** |
| Explainability evidence | Mechanism derivable (before/after, affected volume, comparison group), gated by needing a real incident | The rail-isolated spike pattern is a clean example of exactly this evidence shape (one segment spikes, all others stay flat as a natural control group) | No change to UPI-linked evidence assembly; a validated template for what "good" explanation evidence should look like | **ONLY CALIBRATION VALUE** |
| Counterfactual simulation | `SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT` — zero real basis for any "alternate gateway" parameter (Day 1 Requirements Matrix §E) | Real, measured per-route baseline success/failure/latency rates exist (in a disjoint population) to calibrate the simulator's assumed parameters against, instead of inventing them from nothing | The simulator's mechanics are unchanged, but its assumptions can now cite real measured evidence for their plausibility | **PARTIALLY IMPROVED** — see Q8 below |
| Recovery recommendation | `SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT` for benefit/GMV/risk fields; `target_gateway` `NOT CREDIBLY SUPPORTED` | Same calibration benefit as counterfactual simulation, but `target_gateway` still cannot be populated for any real UPI transaction | Still fully synthetic-enrichment-gated for the UPI pipeline; better-calibrated once built | **ONLY CALIBRATION VALUE** |
| Verification | `SUPPORTED ONLY AFTER SYNTHETIC ENRICHMENT` — static historical CSV, no live continuation | The routing dataset is **also** a static, closed, 7-day historical snapshot — it does not supply a live feed either | No change — post-action verification still requires Aventum's own synthetic continuation regardless of which dataset is in play | **STILL SYNTHETIC** |

---

## Required Conclusion

### 1. What Aventum capabilities became materially stronger because of this dataset?

The **credibility and design-basis of the planned synthetic infrastructure layer**, specifically: gateway-identity design (realistic cardinality and failure-rate spread), latency modeling (a clean 3-tier normal/degraded/timeout shape with a plausible threshold), failure-reason vocabulary (5-way instead of Day 1's 2-way), and incident-shape design (isolated, single-segment, ~1-hour, 8–15×-baseline spikes). None of these strengthen what is *attached to a UPI transaction* — they strengthen the *evidence behind the assumptions* Aventum's synthetic layer will need to make anyway.

### 2. What Aventum capabilities became possible that were previously impossible?

**None, strictly speaking, for the UPI-linked canonical pipeline.** Every capability gap identified in Day 1 (gateway, routing, latency, response/error code, incident ground truth) remains a gap for `upi_transactions_2024` itself, because no row-level attachment is possible (§6 of the audit). What became possible is narrower but real: **a standalone, self-contained demonstration of gateway-level monitoring/RCA/simulation mechanics using the routing dataset's own population**, if Aventum's team ever wants a second, separately-labeled demo track independent of the UPI narrative. This was not available at all in the Day 1 corpus (which had zero transaction-grain infrastructure data of any kind).

### 3. What missing fields remain?

For the UPI-linked pipeline: `gateway_id`, `routing_path`, `routing_policy`, `gateway_latency_ms`, `gateway_response_code`, `gateway_health_state`, `retry_count`, and all `incident.*` ground-truth fields — every one of these still has **zero attached data for any actual UPI transaction**, exactly as in Day 1.

### 4. Which remaining fields are best generated synthetically?

All of them, per §3 — but now with materially better calibration evidence for `gateway_id` (failure-rate spread), `gateway_latency_ms` (3-tier shape), `gateway_response_code` (5-way vocabulary + frequencies), and incident magnitude/duration/isolation. `routing_path`, `routing_policy`, and `retry_count` remain entirely uninformed by any dataset seen so far (including this one) and must be designed from first principles / product judgment, not data.

### 5. Which remaining fields justify searching for another dataset?

**None, on the evidence gathered so far.** The routing dataset already supplies strong calibration for the fields most amenable to external calibration (gateway failure-rate spread, latency shape, response-code vocabulary). Searching for yet another dataset to *also* calibrate these would face the same fundamental problem this dataset has: any external dataset would almost certainly be a disjoint population with no legitimate join to `upi_transactions_2024`, so it could only ever add *more* calibration evidence, not *attached* data — a diminishing-returns pursuit given the calibration bar is already reasonably well met.

### 6. Which fields should NOT be searched for because an additional public dataset would not legitimately help?

`routing_path` and `routing_policy` — these are **decision-log** concepts (why a specific routing choice was made), and no plausible public dataset would expose a real payment processor's internal routing-decision logic (competitive/security-sensitive, exactly as concluded for the equivalent Day 1 gap in [DATASET_ACQUISITION_PLAN.md](DATASET_ACQUISITION_PLAN.md)). `retry_count`/attempt-sequence data is similarly unlikely to be published at transaction grain by any real processor. `incident ground truth` for *this specific corpus* can only ever come from Aventum's own injection layer, by definition — no external dataset can supply ground truth for incidents in data it was never generated against.

### 7. Does this dataset reduce our need for synthetic infrastructure, and by how much?

**No — it does not reduce the need for synthetic infrastructure at all; the UPI pipeline still needs 100% synthetic gateway/routing/latency/health/incident data, exactly as in Day 1.** What it reduces is the **risk that those synthetic values will be arbitrary or implausible** — i.e., it reduces *design risk*, not *data requirement*. This distinction is deliberate and should not be blurred in any downstream summary.

### 8. Does this dataset improve the credibility of the counterfactual simulator? Explain exactly how.

**Yes, specifically and narrowly.** Before this dataset, any assumed parameter in the counterfactual simulator (e.g., "assume synthetic Gateway B has a 98% success rate") was an arbitrary design choice with no supporting evidence anywhere in the corpus. Now, the simulator's assumptions can be **anchored to a real, measured example of route-dependent outcome variance**: a genuine ~2× failure-rate spread across 5 real-world-plausible rails, with a coherent, realistic latency relationship. This means Aventum can say "our synthetic gateway parameters are calibrated against a measured real-world-shaped dataset showing comparable dynamics" instead of "our synthetic gateway parameters were invented." It does **not** mean any specific simulated outcome for any specific UPI transaction becomes more defensible — that number is still entirely a modeled projection (per [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §8), just now built on a better-justified parameter foundation.

### 9. Does this dataset provide descriptive evidence only, or does it support defensible counterfactual reasoning?

**Descriptive evidence only, within its own population — and even there, only correlational, not causal.** It supports **defensible counterfactual reasoning about itself** in a limited sense (e.g., "rails with historically lower failure rates plausibly represent healthier infrastructure"), but even this is confounded by the unobserved routing-assignment mechanism (§7 of the audit: we don't know *why* a transaction landed on a given rail, so we cannot rule out that rail assignment correlates with some other unobserved factor). It supports **no counterfactual reasoning whatsoever about any UPI transaction**, since no join or population-transfer is legitimate. Its correct role is exactly what §7/§8 of the audit already concluded: simulation calibration, not causal ground truth.

### 10. What is the new Aventum Data Readiness Score versus the old 4.7/10?

Recomputed using the same 20-dimension framework as [AVENTUM_DATA_FEASIBILITY.md](AVENTUM_DATA_FEASIBILITY.md) §18. Only dimensions with a measured, justified change are shown; all others (transaction richness, transaction-level availability, temporal resolution, success/failure information, amount/GMV, issuer/bank coverage, payment-method coverage, segmentation depth, merchant/category coverage, geography, device/network, joinability, verification suitability) are **unchanged**, because the routing dataset cannot touch the UPI-linked pipeline directly.

| Dimension | Before | After | Change | Why |
|---|---|---|---|---|
| Failure reason/error information | 2 | 4 | +2 | 5-way vocabulary + realistic frequencies now available as a calibration reference, vs. only NPCI's 2-way aggregate proxy before |
| Gateway visibility | 1 | 4 | +3 | A real, measurably-differentiated gateway-analog (`rail_id`) now exists as calibration evidence, vs. zero evidence anywhere before |
| Latency visibility | 1 | 4 | +3 | A clean, realistic, 3-tier latency shape now exists as calibration evidence, vs. zero evidence anywhere before |
| Routing visibility | 1 | 2 | +1 | `rail_id` hints at a routing *outcome*, but supplies nothing about routing *decisions* (path/policy remain wholly unsupported) |
| Incident suitability (native) | 3 | 5 | +2 | A clean, measured, isolated single-segment spike pattern is now available as an incident-shape template, vs. one uncontextualized cross-sectional outlier before |
| RCA suitability | 4 | 5 | +1 | Independent proof (in a separate population) that gateway-level RCA works cleanly when real infrastructure differentiation exists |
| Counterfactual suitability | 3 | 5 | +2 | Real measured route-dependent outcome variance now available to calibrate simulator assumptions against |

**Before: 4.7/10**

**After: 5.4/10**

**Net improvement: +0.7**

This is a deliberately modest number. It reflects a real, evidence-based improvement to **synthetic-layer design credibility** across 7 of 20 scored dimensions, while honestly recording **zero improvement** to the 13 dimensions that describe what data is actually attached to a UPI transaction — because nothing is, and nothing legitimately can be, per the unconditional no-join finding.

---

## Final Verdict

# USEFUL BUT LIMITED

**Why:** the routing dataset is a genuinely well-constructed, internally consistent synthetic dataset that supplies exactly the kind of infrastructure-behavior evidence Day 1 found completely absent (gateway differentiation, latency shape, failure-reason vocabulary, incident shape) — a real and measurable contribution. It is not a **MAJOR IMPROVEMENT** because it changes zero bits of data actually attached to any UPI transaction (the no-join finding is unconditional and total, unlike any Day 1 join result) — Aventum's core transaction pipeline is exactly as capable today as it was before this dataset arrived. It is not **MOSTLY REDUNDANT** because it materially exceeds what any Day 1 dataset offered for infrastructure calibration (Day 1's best infrastructure-adjacent evidence was a single NPCI cross-sectional outlier; this dataset offers a full measured distribution across 2 million rows). It is not **NOT USEFUL** because the calibration value is real, specific, and directly actionable for the synthetic infrastructure layer's design. **USEFUL BUT LIMITED** is the only verdict that doesn't overstate or understate either side of that finding.
