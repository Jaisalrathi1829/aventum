_Aventum internal report — Day 3 implementation._

# Day 3 Implementation Report — Incident Intelligence

Detect → Diagnose → Explain, built on the frozen Day 2 foundation. This report records what was built, what was measured, and what it is honestly not.

---

## Executive Summary

Day 3 implements one chain end to end: **inject incident → generate simulated outcomes → detect anomaly → collect evidence → rank competing hypotheses → produce explainable RCA.**

The chain works on the real 250,000-row canonical dataset across three scenarios — a gateway degradation, an ordinary window with no incident, and an issuer degradation on a different dimension entirely. It reaches the right conclusion in the first and third, and correctly declines to name a cause in the second.

Three properties matter more than the feature list:

1. **`transactions` was never written to.** Approach B is enforced by a database CHECK constraint, not a convention, so the rejected Approach A is structurally unrepresentable.
2. **The diagnosis path cannot see the answer key.** Ground truth lives in its own table that no detection, evidence, hypothesis, or RCA module imports or names. Verified statically by AST scan and adversarially by re-running RCA with ground truth deleted and corrupted.
3. **The detector was never told which gateway broke.** There is no `gateway_C` literal anywhere in the detection or diagnosis code. It arrives at the answer by measurement, and on a different scenario it arrives at a different answer.

**Status: Day 3 complete.** 362/362 tests pass (260 Day 2 unchanged + 102 new).

---

## Incident Lifecycle

```
CREATED → ACTIVE → DETECTED → DIAGNOSED → RESOLVED → VERIFIED
                                          └── Day 4/5 only; Day 3 never sets these
```

An incident is a declarative definition plus a forward-only lifecycle. Identity is a SHA-256 of the full definition (type, target, window, all three multipliers, seed, lineage, model versions), stored as `incidents.incident_key UNIQUE`. Re-injecting an identical definition resolves to the existing row rather than creating a duplicate.

Backwards transitions raise. Re-running a pipeline over an already-diagnosed incident is legitimate — it is how reproducibility is verified — so the pipeline advances via a forward-only helper that leaves a further-along status untouched.

**Window semantics:** `[incident_start, incident_end)`, half-open, matching Day 2B's health windows. Timezone-aware timestamps are required; a naive one is rejected before it can compare unpredictably against a `timestamptz` column. Zero-width and inverted windows are rejected twice — in validation, and by a CHECK constraint.

---

## Approach B

```
observed FAILED  → simulated FAILED         (always; never rescued)
observed SUCCESS → simulated FAILED w.p. p_add, else SUCCESS
```

A degradation **adds** modelled failures. It never moves, rescues, or reallocates an observed one.

Where `p_add` comes from — the incident's `failure_multiplier` scales the gateway's Day 2B baseline failure probability through `GatewayRuntimeProfile.effective_failure_probability`, the lever Day 2B reserved for exactly this:

```
p_eff = clamp(p_base × failure_multiplier)
p_add = (p_eff − p_base) / (1 − p_base)
```

so the realised cohort rate approaches `p_eff` while every observed failure stays where history put it.

**Enforced, not merely intended.** The database carries:

```sql
CHECK (NOT (observed_status = 'FAILED' AND simulated_status = 'SUCCESS'))
```

A test issues a fully coherent rescue UPDATE — status, response, regime, and the changed flag all consistent — and PostgreSQL rejects it. That is what makes Approach A unrepresentable rather than discouraged.

**Measured result, golden scenario:** affected cohort 264 transactions moved 6.438% → 20.833%; the 1,829-transaction control group sat at 4.921%. Control rows are not approximately unchanged but *exactly* unchanged — every control row carries its observed status and its Day 2B latency and response code through untouched, asserted at zero mismatches.

---

## Simulated Outcome Model

Signals are never drawn independently. Once the simulated status is known, the row is completed by Day 2B's own funnel using a degraded `GatewayRuntimeProfile`:

```
incident state → runtime profile → failure probability → response family
                                → latency regime → latency value
```

One state change therefore moves failure rate, latency, and response mix together. Measured on the affected cohort: mean latency rose above its Day 2B baseline and infrastructure-side responses (`PROCESSING_ERROR`, `TIMEOUT`) rose with it — both asserted, not assumed.

Four coherence rules are database constraints, so a generator bug cannot persist a nonsensical row: a modelled success is always `APPROVED`, a `TIMEOUT` response always carries `TIMEOUT`-regime latency, `outcome_changed` must actually describe the two status columns, and the Approach B rule above.

**Scope:** one row per transaction inside an incident window — not one per canonical transaction. Outside the window the effective outcome simply *is* the observed outcome, and materialising 250,000 unchanged copies would add no information while inviting exactly the confusion the layer exists to prevent.

---

## Detection Methodology

The detector receives a window and a database. It is not told what broke.

For every cohort along six single dimensions (gateway, sender_bank, payment_method, region, device, network) plus three intersections, it compares the incident window against that same cohort's own pre-incident baseline using a **pooled two-proportion z test**. The baseline is all history before the incident — months rather than days, which buys a far more stable per-cohort rate and cannot leak incident-period information backwards.

The effective-outcome surface resolves by outer join:

```sql
effective_status = COALESCE(simulated_incident_outcomes.simulated_status, transactions.status)
```

with `outcome_source` (OBSERVED / SIMULATED) travelling alongside, so the two epistemic layers are never silently merged. Passing `incident_id = NULL` yields a pure-observed surface — which is exactly what the no-incident scenario needs.

**Ranking** is `significance × effect_factor`, not significance alone. Sigma by itself rewards huge cohorts with trivial moves, which is the classic way a multi-dimensional detector produces confident nonsense; the effect term is linear up to a 15pp reference delta and clamped after.

---

## Alerting Discipline

Four gates, all explicit constants rather than literals buried in a query:

| Gate | Value | Why |
|---|---|---|
| Minimum cohort size (window) | 100 | A 3-transaction cohort failing twice reads as 67% and outranks a real outage |
| Minimum baseline cohort size | 300 | An unstable baseline makes any comparison meaningless |
| Minimum absolute rate delta | 2pp | Statistical significance on a trivial move is not an incident |
| Minimum significance | 3σ | — |

**Redundancy suppression:** when a narrower cohort sits inside an already-reported broader one and adds under 2σ of strength, it is marked suppressed and names its suppressor rather than emitting a second alert. Without this, one gateway degradation produces an alert for every `gateway × bank × method` cell it happens to touch. Suppressed candidates are still persisted, for audit.

---

## Evidence Model

Eight evidence types, each a named analytical step recorded in `evidence_source`, each carrying its `source_layer`:

| Type | What it establishes |
|---|---|
| `failure_rate` | The headline movement, with sigma |
| `latency` | Whether the cohort also slowed |
| `response_mix` | Issuer-side vs infrastructure-side shift |
| `control_comparison` | Whether the peers moved too |
| `blast_radius` | How much of the dimension is affected |
| `temporal_alignment` | Whether the change is confined to the window |
| `confounding_check` | **Whether the cohort moves on its own** — see below |
| `gmv_impact` | Value at risk, from observed `transactions.amount` |

No LLM writes a row in `incident_evidence`. An RCA statement that cannot cite an `evidence_id` from this table is not a finding Aventum will make.

### The confounding check

This one was added after a measured failure, and it is the most important part of the diagnosis layer.

A degraded gateway drags every dimension that intersects it. In the first working run of the golden scenario, `region=Delhi` (4.01σ) and `network=5G` (4.85σ) were flagged as anomalies purely because they carried gateway_C traffic. On signal strength alone those shadows are indistinguishable from real causes, and they inflated rival hypotheses enough to drag the verdict down to UNCERTAIN.

The fix asks a causal-flavoured question: **does this cohort's anomaly survive removing the leading suspect on another dimension?**

```
independence = residual_delta_after_exclusion / original_delta
```

A real cause keeps most of its movement. A shadow collapses. Applying it moved the golden scenario from UNCERTAIN (0.53) to **CONFIDENT (0.64)** and demoted both spillover hypotheses with explicit contradicting evidence — without any hard-coded knowledge of which cohort was which.

---

## Hypothesis Model

Five categories are **always** evaluated — gateway, issuer, payment-method, network-segment, and systemic — including those nothing supports, which score near zero and say why. A hypothesis set that omits what was ruled out is a ranking of confirmations, not reasoning.

Score is a documented weighted sum, persisted in `score_components` for audit:

| Component | Weight | Measures |
|---|---|---|
| independence | 0.30 | Survives excluding the leading rival |
| signal | 0.30 | Statistical strength, saturating at 12σ |
| divergence | 0.20 | Subject movement vs its control group |
| localisation | 0.15 | Inverse blast radius |
| temporal | 0.05 | Confined to the window |
| response tilt | ±0.15 | Bounded modifier, applied after |

Two signals do the real discriminating work, and neither names a specific gateway or bank:

1. **Which dimension carries the localised, control-divergent anomaly.** A gateway fault shows as one gateway diverging from its peers; an issuer fault shows as one bank diverging across all gateways.
2. **The response-mix tilt.** Infrastructure failures skew the failure mix toward `PROCESSING_ERROR`/`TIMEOUT`; an issuer declining transactions does not. Measured as the change in `infrastructure_side / failures`, this supports infrastructure-flavoured hypotheses and counts against issuer-flavoured ones.

The systemic hypothesis gets the same residual test: a large issuer degrading looks fleet-wide on breadth alone, so the honest question is whether the population still moves once the leading cohort is removed. In the alternative scenario it does not, and systemic correctly scores 0.

---

## RCA

Produces `verdict · predicted_root_cause · confidence · summary · explanation · affected_population · control_population · supporting_evidence_ids · contradicting_evidence_ids · alternatives_considered · rca_fingerprint`.

**Confidence** reflects decisiveness, not just strength: a hypothesis that barely beats its runner-up earns less than one that wins outright.

```
confidence = top_score × (0.5 + 0.5 × margin),  margin = (top − second) / top
```

**Verdict** is `CONFIDENT` (≥0.60), `UNCERTAIN` (≥0.35), or `INSUFFICIENT_EVIDENCE`. The last sets `predicted_root_cause` to NULL — enforced by a CHECK constraint that a named cause and a declining verdict cannot coexist. A diagnosis engine that always names a cause is not more useful than one that can say "I don't know"; it is just less honest about the cases where it was guessing.

Explanations cite evidence by ID (`E41`), state what the runner-up was and why it lost, and close by noting that no value in them was generated by a language model.

---

## Truth-Model Isolation

Ground truth lives in `incident_ground_truth`, its own table, guarded by `CHECK (is_evaluation_only = true)`.

**This deviates from the Day 3 contract**, which sketched `ground_truth_root_cause` as a flagged column on `incidents`. A flag on a shared row is a convention, and conventions leak through an incautious `SELECT *`. A separate table makes the boundary structural. Since ground-truth isolation is the single invariant the project's credibility rests on, it is worth a table.

Verified three ways:

1. **Statically** — an AST scan (docstrings and comments stripped, since the modules legitimately *discuss* isolation in prose) asserts that `detect.py`, `evidence.py`, `hypothesis.py`, `rca.py`, and `metrics.py` contain no executable reference to the table, the model, or the field.
2. **Adversarially, deleted** — RCA re-run with all ground truth deleted produces a byte-identical `rca_fingerprint`.
3. **Adversarially, corrupted** — ground truth rewritten to blame `gateway_A` with a wrong cause string; RCA still returns `gateway_C` and the same fingerprint.

`evaluation.py` is the only module that reads ground truth, and it takes an already-produced `RcaResult` as input. The ordering is enforced by the data flow, not by discipline.

The Day 4 handoff never exposes ground truth — asserted by scanning the serialised payload.

---

## Provenance

Every simulated outcome traces:

```
simulated outcome → incident → generation run → source ingestion run → dataset registry → source SHA-256
```

Zero broken links, asserted. Every Day 3 table carries machine-enforced provenance (`is_synthetic` / `is_simulated` / `is_evaluation_only`, each `NOT NULL DEFAULT true CHECK (… = true)`), and each of the eight is adversarially tested against a clearing UPDATE — with a full pipeline run first, so the tables actually hold rows and the test cannot pass for the wrong reason.

Evidence records never flatten their layer: of 88 evidence rows across the three scenarios, 11 are `OBSERVED` (GMV, from authoritative transaction amounts) and 77 are `SIMULATED`.

---

## Reproducibility

Three fingerprints, all SHA-256 over ordered content, all excluding surrogate IDs and wall-clock time so a clean rebuild reproduces them:

| Fingerprint | Covers |
|---|---|
| `simulation_fingerprint` | The ordered simulated rows |
| `rca_fingerprint` | Verdict, cause, confidence, hypothesis ranking, evidence |
| `analysis_fingerprint` | Simulation + ranked anomalies + RCA |

Day 3 derives its own digest keyed on `transaction_id | incident_key | seed | model_version | config_version`, using Day 2B's audited primitives. Keying on the incident is deliberate: draws must change when the *incident definition* changes, not only when the transaction does. Changing the seed, any multiplier, the window, or the target changes `incident_key`, which changes every draw, which changes the fingerprint — each asserted.

Day 2B's `LANE_RESERVED` was left genuinely unused; Day 3 needed three independent draws per transaction and its own incident-keyed stream, so a separate digest with four fresh lanes was cleaner than crowding one reserved lane.

Verified: three consecutive runs of the alternative scenario produced identical fingerprints, including after a truncate and a reordering.

---

## Golden Scenario Results

```
gateway_C · 2024-06-01 → 06-04 IST · multipliers 3.5 / 2.2 / 6.0
```

| Metric | Value |
|---|---|
| Rows in window / simulated / changed | 2,093 / 2,093 / 36 |
| Affected cohort | 264 txns, 6.438% → **20.833%** |
| Control group (A, B, D, E) | 1,829 txns, **4.921%** |
| Detection | **9.26σ**, CRITICAL, ranked #1 |
| Cohorts scanned / reported | 247 / 6 |
| Evidence records | 40 |
| RCA | **CONFIDENT, 0.6396** — "Payment gateway gateway_C is degraded" |
| Hypothesis ranking | gateway 0.931 › network 0.583 › issuer 0.450 › systemic 0.026 › method 0.000 |
| Ground-truth match | ✅ correct |
| Simulation fingerprint | `4fb3ef5dc0df0b947e68a6a2734f96ab00058d3976486fdb98d7d4748c8e0602` |

The target band was 20–25% and the measured rate is 20.83% — the low end. The gap from the 22.5% design target is sampling variation on 245 observed successes, not a calibration error: the multiplier that would produce 22.5% in expectation is 3.49, and 3.5 was used. Signal landed at 9.26σ against a 9–13σ expectation.

---

## No-Incident Results

```
2024-09-01 → 09-04 IST, nothing injected
```

248 cohorts scanned, **0 anomalies reported**, 0 high-severity alerts, 0 evidence records. All five hypotheses score 0.000. RCA returns `INSUFFICIENT_EVIDENCE` with `predicted_root_cause = NULL`.

This is the control that makes the other two results meaningful. A detector that finds a critical incident in ordinary traffic is worse than no detector.

---

## Alternative-Scenario Results

```
issuer degradation on SBI, across all gateways · multipliers 4.5 / 1.1 / 1.0
```

| Metric | Value |
|---|---|
| Affected cohort | 552 txns, 4.871% → **21.920%** |
| Control group | 1,541 txns, **4.932%** |
| Detection | `sender_bank=SBI` at **17.82σ**, ranked #1 |
| RCA | **CONFIDENT, 0.7393** — "Issuer/bank SBI is declining or failing transactions" |
| `predicted_gateway_id` | **None** |
| Hypothesis ranking | issuer 0.893 › network 0.307 › **gateway 0.223** › method 0.149 › systemic 0.000 |
| Blamed gateway_C | **No** |

This is the anti-overfit proof. Same engine, same thresholds, same code path — a different cause, correctly named, with `gateway_degradation` ranked third carrying four contradicting evidence items. The timeout multiplier is deliberately 1.0 here: an issuer problem keeps its failures in the issuer-side response families, and that difference is a genuine diagnostic signal rather than a thumb on the scale.

---

## Performance

Measured on the real 250,000-row dataset:

| Stage | Golden | No-incident | Alternative |
|---|---|---|---|
| Simulation | ~330 ms | — | ~340 ms |
| Detection | 2,307 ms | 2,873 ms | 12,897 ms |
| Evidence | 810 ms | 0 ms | 1,005 ms |
| RCA | 0.3 ms | 0.1 ms | 0.3 ms |

Detection dominates: 18–20 set-based aggregate queries over 250,000 rows. There are **no per-transaction queries** anywhere — the simulator fetches its whole window in one statement and does all generation in memory, and detection and evidence share a memoising `MetricStore` so the same aggregate is never computed twice.

The alternative scenario's 12.9s outlier is the cost of 18 reported anomalies each pulling a residual (confounding) query, versus 6 in the golden case. Acceptable for a demo; the obvious optimisation is batching residuals, and it is not needed yet.

---

## Limitations

Stated plainly, because a report that lists only successes is not useful.

1. **Detection cost scales with reported anomalies.** Each subject triggers residual queries. A very noisy window would be slow.
2. **Control latency is a volume-weighted mean of per-cohort p95s, not a true pooled p95** — a pooled percentile cannot be recovered from per-cohort percentiles. The metric is named `latency_p95_weighted_mean` so the name itself discloses the approximation.
3. **The confounding check excludes one leading suspect, not several.** A genuine two-cause incident would not be fully disentangled.
4. **Intersections are limited to three explicit combinations.** The full power set over six dimensions collapses below usable sample sizes; the allow-list is a deliberate bound, but it means an unusual interaction outside those three would be missed.
5. **Baseline is all pre-incident history.** Stable, but it does not model seasonality — a genuine weekday/weekend effect would be absorbed into the baseline rather than adjusted for.
6. **The 20.83% realised rate sits at the low edge of the 20–25% band.** Within sampling variation for n=245, but a tighter target would need a larger cohort or a slightly higher multiplier.
7. **Day 3 inherits every Day 2 limitation** — the infrastructure layer is synthetic, the incident is injected, and neither is a claim about real payment infrastructure.

---

## Honesty Boundaries

Unchanged from Day 2, restated because Day 3 is the first layer that produces conclusions:

- An injected incident is a **synthetic construction**. It never occurred in history, and a demo must never imply otherwise.
- A simulated outcome is a **modelled** outcome, never a measured one.
- Attribution of observed outcomes to synthetic gateways remains a calibrated construction. Aventum constructs a plausible infrastructure world consistent with observed data — it does not discover one.
- No value in this layer is real Razorpay telemetry, routing, or incident data.
- GMV figures use authoritative observed `transactions.amount`; *which* transactions failed is modelled. Evidence records label this explicitly.

---

## Day 4 Interface

Day 4 consumes Day 3 through `aventum_incident.handoff` and nothing else — it must not reconstruct a diagnosis by querying raw tables, which would couple it to Day 3's schema and let it assemble a different version of the same conclusion.

```python
build_handoff(session, analysis_run_id) -> Day4Handoff
ranked_hypotheses(session, analysis_run_id) -> list[dict]
```

| Object | Fields |
|---|---|
| `IncidentView` | `incident_id · incident_name · incident_type · affected_gateway · affected_segment · start · end · severity · status · provenance` |
| `SimulatedOutcomeSummary` | `incident_id · simulation_run_id · rows_in_window · rows_simulated · rows_changed · simulation_fingerprint · provenance` |
| `DetectionView` | `anomaly_id · severity · anomaly_score · significance_sigma · cohort_key · affected_population · baseline_metrics · current_metrics · detection_window · gmv_at_risk · rank` |
| `EvidenceView` | `evidence_id · evidence_type · metric · baseline · current · delta · significance_sigma · cohort · control · source_layer · evidence_source · explanation` |
| `RcaView` | `incident_id · analysis_run_id · verdict · predicted_root_cause · predicted_hypothesis_type · predicted_gateway_id · predicted_segment · confidence · summary · explanation · supporting_evidence_ids · contradicting_evidence_ids · alternatives_considered · affected_population · control_population · rca_fingerprint` |

Ground truth is **not** in this surface, by design.

---

## Test Coverage

**362 tests, all passing** — 260 Day 2 (unchanged, none removed or weakened) + 102 Day 3.

| Area | Coverage |
|---|---|
| Statistics | Hand-computed z, empty samples, improvement, effect saturation, ranking discipline, severity bands |
| RNG | Stability, per-input sensitivity, lane independence, proof it uses SHA-256 not salted `hash()` |
| Incident | Identity, idempotency, timezone-equivalence, 7 invalid definitions, lifecycle, DB-level window guard |
| Approach B | Observed table fingerprint-identical, no rescues, DB rejects a coherent rescue, target band, controls exactly unchanged, signals carried through |
| Coherence | Status/response/regime/latency, coupled movement of failure + latency + response mix |
| Windows | Half-open at both boundaries, nothing outside the window simulated |
| Provenance | 8 tables adversarially tested, ground-truth flag, full lineage chain |
| Determinism | Re-simulation, replacement-not-accumulation, seed sensitivity, multiplier sensitivity, end-to-end fingerprint |
| Detection | Finds the culprit unaided, signal strength, no false positives, sample-size gates, suppression correctness, dense ranking |
| Evidence | Type coverage, source layer, exact metric agreement, control exclusion, control stability, GMV sourcing, confounding |
| Hypotheses | All categories evaluated, correct winner, dense ranking, citation validity, contradiction present, components persisted |
| RCA | Correct naming, evidence citation, alternatives, declining, alternative scenario |
| Ground truth | AST scan + deleted + corrupted |
| Handoff | All interfaces, no ground-truth leakage, serialisable |
| Flagship | Full chain as one story |

Two fixture bugs were found and fixed during this work, both worth recording because both would have produced *passing but meaningless* tests:

- **Modular collision (failures).** `status = i % 20` alongside `day = 1 + (i % 12)` share a factor, confining every failure to days 1, 5 and 9 — the incident window contained no failures at all and the control rate was 0.0.
- **Modular collision (banks).** `banks[i % 8]` alongside `day = 1 + (i % 12)` has `gcd(8,12)=4`, so each day saw only two of eight banks and **SBI never occurred in the incident window** — the affected cohort was empty and the issuer scenario silently tested nothing.

Both were replaced with independently salted hash-derived attributes. The lesson generalises: modular strides in fixtures are not independent of each other, and a test built on them can pass while measuring nothing.

---

## Contract Deviations

Recorded against [DAY3_IMPLEMENTATION_CONTRACT.md](DAY3_IMPLEMENTATION_CONTRACT.md):

| Contract | Implemented | Why |
|---|---|---|
| `ground_truth_root_cause` as a flagged column on `incidents` | Separate `incident_ground_truth` table | Structural isolation beats a convention for the project's central invariant |
| `incident_evaluation` as one table | Split into `incident_anomalies`, `incident_hypotheses`, `incident_rca_results` | A ranked hypothesis set with supporting *and* contradicting evidence cannot be represented honestly in one flat row |
| — | Added `incident_simulation_runs`, `incident_analysis_runs` | Mirrors the Day 2A/2B run-anchor pattern; carries fingerprints and timings |
| — | Added `confounding_check` evidence type | Added in response to a measured failure; see Evidence Model |

All contract-required fields are preserved across the split tables. All nine acceptance-gate conditions are met.

---

## Files

**New package** `backend/aventum_incident/` — `__init__`, `constants`, `models`, `rng`, `statistics`, `incident`, `simulate`, `metrics`, `detect`, `evidence`, `hypothesis`, `rca`, `evaluation`, `pipeline`, `handoff`, `cli`.

**New migration** `0004_incident_intelligence.py` — 9 tables, no change to any existing table.

**New tests** `tests/test_incident_intelligence.py` — 102 tests.

**Modified** — `migrations/env.py` (register Day 3 metadata), `tests/conftest.py` (truncate Day 3 tables).

---

## Reproducing

```bash
cd backend && .venv/Scripts/python -m alembic upgrade head
```

```bash
cd backend && .venv/Scripts/python -m aventum_incident.cli scenarios
```

`golden`, `quiet`, `alternative` run scenarios individually; `status` lists incidents and analysis runs; `handoff <analysis_run_id>` prints the Day 4 object as JSON.

---

# DAY 3 COMPLETE
