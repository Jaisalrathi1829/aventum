# Routing Dataset Decision

Final classification, updated acquisition reasoning, and final recommendation for the Nigerian Card Payment Dataset for Predictive Routing, built on [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md), [ROUTING_DATASET_SCHEMA_MAPPING.md](ROUTING_DATASET_SCHEMA_MAPPING.md), and [AVENTUM_DATASET_DELTA.md](AVENTUM_DATASET_DELTA.md).

---

## 14. Final Classification

# B. USE AS SIMULATION / CALIBRATION DATA

**Quantitative justification:**

- **Not A (Integrate into Aventum data model):** every candidate join key was tested and every one failed unconditionally — zero `transaction_id` overlap, zero calendar-date overlap (routing dataset: Jan 1–7, 2025; UPI dataset: all of 2024), incompatible currencies with no stated conversion, no bank field of any kind, and disjoint merchant vocabularies ([ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §6). There is no legitimate compatibility to integrate.
- **Is B (Use as simulation/calibration data):** the dataset cannot be joined, but it **materially improves the realism of infrastructure behavior** Aventum's synthetic layer will need to invent anyway, with specific, measured numbers: a genuine ~2× gateway-level failure-rate spread (1.85%–3.83% across 5 rails, holding up within every payment channel — not noise, unlike the UPI dataset's own 0.28–0.51-point bank-level spread), a clean 3-tier latency shape (≈500ms success / ≈900ms non-timeout failure / ≈3,986ms timeout, with a sharp ~2,000ms floor), a realistic 5-way failure-reason vocabulary, and a measured, isolated, single-segment, ~1-hour, 8–15×-baseline incident-spike pattern. This is exactly the "cannot be joined but materially improves realism" case classification B is defined for.
- **Not C (Reference only, should not drive numerical simulation):** classification C would apply if this dataset only informed vocabulary/assumptions in a general sense. It does more than that — the *specific measured magnitudes* (the failure-rate spread, the latency-tier ratios, the spike magnitude/duration) are precise enough and well-behaved enough to legitimately parameterize the synthetic layer's actual numeric ranges, not just its label set. Restricting it to "vocabulary only" would under-use a dataset whose strongest contribution is exactly these numeric shapes.
- **Not D (Do not use):** the calibration value is real, specific, and directly actionable — discarding it would mean designing the synthetic infrastructure layer with arbitrary, unevidenced numbers instead of numbers grounded in a measured, internally-consistent (if synthetic) reference population.

**Boundary condition, restated from every prior document in this audit:** classification B means this dataset informs *parameter design*. It never means any value from this dataset is written into a `upi_transactions_2024`-linked row, and no simulation output may cite this dataset as if it were evidence about a specific UPI transaction.

---

## 15. Updated Data Acquisition Plan

### 1. Which missing Aventum fields does this dataset solve?

**None outright.** "Solve" would require real, attached data for actual UPI transactions, and no join exists (§14). Nothing is solved in that sense.

### 2. Which does it partially solve?

In the calibration sense only — moving each from "zero evidence, parameters must be invented from nothing" to "parameters can be evidence-based": **gateway identity design** (`gateway_id` failure-rate-spread magnitude), **latency design** (`gateway_latency_ms` 3-tier shape), **response/error-code vocabulary** (`gateway_response_code`, 5-way taxonomy replacing Day 1's NPCI-only 2-way proxy), and **incident-shape design** (magnitude/duration/isolation parameters for the incident-injection layer).

### 3. Which remain completely missing?

`routing_path`, `routing_policy` (no decision-log concept exists in this dataset at all — `rail_id` is an outcome attribute, not a decision record), `retry_count`/attempt-sequence behavior (confirmed structurally absent, [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §2), an explicit `gateway_health_state` label (only a derivable proxy exists), and — for the UPI-linked pipeline specifically — every infrastructure/incident field remains entirely unattached to any actual transaction row.

### 4. Which should now be observed from this dataset?

**None.** Nothing from this dataset should be treated as an "observed" fact within Aventum's canonical schema for any UPI-linked purpose. Every value from this dataset that informs Aventum's design does so as a **calibration parameter**, tagged `synthetic` (parameter-informed) in the canonical schema, never as `observed`.

### 5. Which should still be synthetic?

All of `gateway_id`, `routing_path`, `routing_policy`, `gateway_latency_ms`, `gateway_response_code`, `gateway_health_state`, `retry_count`, and every `incident.*` field remain synthetic, exactly as scoped in [AVENTUM_CANONICAL_SCHEMA.md](AVENTUM_CANONICAL_SCHEMA.md) — this dataset changes *how well-evidenced* four of those synthetic designs can be (`gateway_id`, `gateway_latency_ms`, `gateway_response_code`, incident shape), not *whether* they must be synthetic.

### 6. Does Aventum still need additional datasets?

**Not urgently.** The calibration need for the fields most amenable to external calibration (gateway failure-rate spread, latency shape, failure-reason vocabulary, incident shape) is now reasonably well met. Pursuing further external datasets for these same fields would face the identical structural problem this one has — any external payment dataset is overwhelmingly likely to be a disjoint population with no legitimate join to `upi_transactions_2024` — so additional search would yield diminishing calibration returns, not new attached data.

### 7. If yes, exactly what type of dataset is still missing?

If pursued at all (low priority): a dataset that exposes **actual routing decisions**, not just routing outcomes — e.g., a log showing a transaction evaluated against multiple candidate gateways with scores and a final selection reason. Neither this dataset nor anything in the Day 1 corpus contains that concept; `rail_id` only ever shows the single gateway that was ultimately used, never the alternatives considered or the policy that chose it. This would be needed to move `routing_path`/`routing_policy` out of "first-principles design" into "calibrated design" — but per §6, this is not worth active acquisition effort given the same joinability ceiling would apply.

### 8. If no, why not?

Because the fundamental constraint is not "we lack a good enough calibration dataset" — it's that **no external dataset, however good, can ever supply data actually attached to a UPI transaction**, since that would require either (a) the external dataset being generated against the exact same transaction population (impossible for any dataset not built by Aventum itself), or (b) a legitimate join key, which no realistic payment dataset would share with an unrelated UPI extract (per the exhaustive, unconditional non-match in [ROUTING_DATASET_AUDIT.md](ROUTING_DATASET_AUDIT.md) §6). The remaining gap (`routing_path`/`routing_policy`/`retry_count`/explicit `gateway_health_state`) is better closed by product/design judgment within the synthetic-infrastructure-layer build, informed by what calibration evidence already exists, rather than by further dataset acquisition.

---

## 16. Final Recommendation

| Aventum requirement | New dataset provides? | How useful? | Use in final system? |
|---|---|---|---|
| Gateway | Yes — `rail_id`, real ~2× failure-rate differentiation | High, as calibration only | **Yes** — informs synthetic `gateway_id` design; never attached to UPI rows |
| Routing (path/policy) | No — only an outcome attribute, no decision-log concept | None | **No** |
| Latency | Yes — `latency_ms`, clean 3-tier outcome-dependent shape | High, as calibration only | **Yes** — informs synthetic `gateway_latency_ms` design |
| Response code | Yes — `gateway_response`, 6-value realistic vocabulary | Medium-high, as vocabulary/frequency reference | **Yes** — informs synthetic `gateway_response_code` vocabulary |
| Error code | Yes — `error_code`, redundant with response code (confirmed 1:1) | Medium — confirms one canonical field suffices, adds no independent signal | **Yes**, same role as response code (do not add as a second canonical field) |
| Failure reason | Yes — 5-way taxonomy (funds/issuer-generic/issuer/technical/timeout) | Medium-high, as vocabulary/frequency reference | **Yes** — richer than Day 1's NPCI-only 2-way proxy |
| Incident ground truth | No explicit label — only a discovered, unlabeled spike pattern | Medium, as a shape template (magnitude/duration/isolation) | **Yes**, as design input only — never as actual ground truth |
| Counterfactual simulation | Indirectly — real measured route-outcome variance to anchor assumed parameters | Medium — improves assumption credibility, not mechanism or attachability | **Yes**, indirectly, once the synthetic layer is built using these calibrations |
| Recovery recommendation | Indirectly — same calibration chain as counterfactual simulation | Low-medium | **Yes**, indirectly, downstream of the synthetic layer |
| Verification | No — also a static, closed, 7-day snapshot with no live continuation | None | **No** |

### Recommended data architecture

```text
upi_transactions_2024                    Nigerian Card Payment Dataset
(observed, canonical                     for Predictive Routing
 transaction backbone)                   (observed, independent population —
        │                                 UNCONDITIONALLY never joined to
        │                                 the line at left — no shared key
        │                                 exists on any dimension)
        │                                         │
        │                                         ▼
        │                          Infrastructure-behavior calibration
        │                          (gateway failure-rate spread ~1.85–3.83%,
        │                           latency 3-tier shape 500/900/3986ms,
        │                           5-way response/error vocabulary,
        │                           isolated-spike incident shape)
        │                                         │
        ▼                                         │
Canonical transaction table  ◄── parameters inform design of ──┘
        │
        ▼
Synthetic infrastructure layer
(gateway_id, routing_path, routing_policy,
 gateway_latency_ms, gateway_response_code,
 gateway_health_state — attached to real
 upi_transactions_2024 transaction_ids;
 values are generated, using the calibrated
 parameters above as design inputs; every
 value tagged `synthetic`, never `observed`)
        │
        ▼
Incident-injection layer
(incident_id / incident_start / incident_end /
 incident_type / affected_segment /
 ground_truth_root_cause — magnitude, duration,
 and single-segment isolation modeled on the
 routing dataset's measured spike pattern;
 used only for offline evaluation, never fed
 into the diagnosis pipeline as input)
        │
        ▼
Detect → Diagnose → Explain → Simulate → Recommend → Human Approve → Execute → Verify → Audit
```

**Two independent observed datasets, one canonical backbone.** The routing dataset never enters the transaction table, never gains a foreign key to any UPI row, and is never presented in any output as evidence about a specific UPI transaction. Its entire contribution flows through one channel — informing the *parameters* chosen when the synthetic infrastructure and incident layers are designed — and that channel is explicitly logged (in the canonical schema's `synthetic` classification and in this document) so the distinction between "calibrated" and "observed" is never lost downstream.
