_Aventum internal review — independent Day 3 architecture, intelligence, and production-readiness gate. Review-only: no production code, schema, migration, or test was modified._

# Day 3 Architecture Review

Every conclusion below is backed by a live query, an executed test, a mutation experiment, or direct code inspection. Claims from `DAY3_IMPLEMENTATION_REPORT.md` were treated as hypotheses to verify, not as evidence.

---

## Executive Verdict

# APPROVED WITH REQUIRED FIXES

Day 3 is a genuinely strong incident-intelligence layer, not a one-scenario demo. It generalises: across **six independently injected incidents spanning three different gateways and two different issuers, the top-ranked cohort was the true cause 6/6 times**, and across **1,994 cohort-tests in eight quiet windows it raised zero false positives**. Observed history is provably untouched — the content hash of `transactions` is byte-identical before and after nine incident injections. Ground-truth isolation is structural and survives adversarial deletion and corruption.

**0 P0 issues.** Two P1 issues must be addressed before Day 4, because Day 4 consumes precisely the two surfaces they affect:

1. **Alert precision is low** — a single gateway incident emitted 22 alerts, 8 of them high-severity, of which exactly 1 was the cause (mean precision across incidents: **16.5%**). The machinery to fix this (the confounding/independence test) already exists but is applied only to hypothesis scoring, never to the alert surface Day 4 and Day 5 read.
2. **RCA confidence is weakly calibrated against evidence strength** — a 5.16σ incident received *higher* confidence (0.694) than the 9.26σ flagship (0.640). Day 4 bounds recommended actions by confidence, so weaker evidence could authorise a larger action.

Neither undermines correctness or honesty. Both are integration-surface defects, and both are narrow.

---

## 1. Day 2 Regression

Full suite, single run, no concurrent load:

| Metric | Result |
|---|---|
| Total / passed / failed / skipped | **362 / 362 / 0 / 0** |
| Runtime | 357.56 s |

| Day 2 invariant | Pre-review | Post-review | Status |
|---|---|---|---|
| `transactions` rows | 250,000 | 250,000 | unchanged |
| Observed failures | 12,376 | 12,376 | unchanged |
| **Observed content MD5** | `13965d76407219517a57702df5f24226` | `13965d76407219517a57702df5f24226` | **byte-identical** |
| Canonical fingerprint | `12dec963…f4b8` | `12dec963…f4b8` | unchanged |
| Synthetic assignments | 250,000 | 250,000 | unchanged |
| Generation staleness | `CURRENT` | `CURRENT` | unchanged |
| `baseline-v1` profiles | 5 gateways | identical values | unmutated |
| Alembic head | 0004 | 0004 | — |

The content hash covers `transaction_id | status | amount | timestamp` for all 250,000 rows ordered by id — a same-size mutation would change it. It survived **nine incident injections, twelve analysis runs, eight quiet scans, five idempotency repeats, a deliberately crashed pipeline, and a four-thread concurrency race**.

**No Day 2 regression.**

---

## 2. Implementation Inventory

Verified by execution and schema inspection, never from documentation.

| Capability | Expected | Actually implemented | Evidence | Status |
|---|---|---|---|---|
| Incident model | Definition + identity + lifecycle | `incidents` table, SHA-256 `incident_key UNIQUE`, 6-state forward-only lifecycle | 5 repeats → 1 row; backwards transition raises | **IMPLEMENTED** |
| Ground-truth isolation | Evaluation-only | Separate `incident_ground_truth` table, `CHECK (is_evaluation_only = true)` | AST scan: 5 diagnosis modules clean | **IMPLEMENTED** |
| Simulated outcomes | Approach B layer | `simulated_incident_outcomes`, 14,651 rows, DB-enforced no-rescue | 0 rescues in 12,558+ rows | **IMPLEMENTED** |
| Simulation runs | Reproducibility anchor | `incident_simulation_runs` + fingerprint | identical fp over 5 repeats | **IMPLEMENTED** |
| Anomaly detector | Deterministic, multi-dimensional | `detect.py`, 247 cohorts / 9 dimension sets, pooled two-proportion z | 6/6 correct top-rank | **IMPLEMENTED** |
| Evidence engine | Traceable, provenance-tagged | 8 types × 41 records each, 0 missing source/explanation | live query | **IMPLEMENTED** |
| Hypothesis engine | Competing, with contradiction | 5 categories always scored, supporting **and** contradicting IDs | every run has 5 | **IMPLEMENTED** |
| RCA engine | Cited, confident, able to decline | `incident_rca_results`, verdict CHECK ties NULL cause to declining verdict | 2 declines observed | **IMPLEMENTED** |
| Analysis runs | Audit anchor + timings | `incident_analysis_runs` with stage timings + fingerprint | 12 runs | **IMPLEMENTED** |
| Provenance chain | To source SHA-256 | 14,651/14,651 resolve | live join | **IMPLEMENTED** |
| Day 4 outputs | Structured, no raw SQL | `handoff.build_handoff()` → 5 typed objects | JSON emitted | **IMPLEMENTED** |
| Alert lifecycle/hysteresis | active / recovering / resolved | **not present** — each run is independent | anomaly_id changes 73→84 for same cohort | **MISSING** (deferred, see §11) |

---

## 3. Incident Model

`incident_key` is a SHA-256 over the full definition — type, target, segment, window (rendered as UTC instants), all three multipliers, lineage, seed, and both model versions.

| Property | Test | Result |
|---|---|---|
| Idempotent under repeat | 5 sequential creates | 1 incident, 1 sim run, 2,093 outcomes, 1 ground-truth row |
| Timezone-equivalence | IST vs UTC spelling of same window | identical key |
| Sensitivity | 7 field mutations | all produce distinct keys |
| Lifecycle forward-only | `DIAGNOSED → CREATED` | raises `IncidentLifecycleError` |
| Re-run tolerance | pipeline over diagnosed incident | `ensure_status` advances only forward |
| Window validity | zero-width / inverted | rejected in validation **and** by CHECK |
| Naive timestamps | tz-naive datetime | rejected before comparison |

**Concurrency.** Four threads synchronised on a `threading.Barrier` all called `create_incident` simultaneously: one created, three resolved to the existing row, **0 exceptions, 1 row persisted**. Integrity is additionally guaranteed by `UNIQUE(incident_key)` regardless of timing. There is no advisory lock, so under contention heavier than tested a caller could in principle surface an `IntegrityError` rather than the existing incident — P2, and the same class of item Day 2A's review recorded for ingestion.

**Crash safety.** A pipeline interrupted by an exception after simulation but before commit left **0 rows and 0 orphaned incidents** — `simulated_incident_outcomes` was 14,651 before and after. Transaction boundaries are correct.

---

## 4. Approach B

The strongest form of the guarantee holds: not "we didn't write to `transactions`" but "the bytes are identical".

```
observed content MD5, before all review activity : 13965d76407219517a57702df5f24226
observed content MD5, after                       : 13965d76407219517a57702df5f24226
```

| Check | Result |
|---|---|
| Day 3 code writing to `transactions` | none (grep across package) |
| `observed FAILED → simulated SUCCESS` rescues | **0** of 14,651 |
| DB rejects a fully coherent rescue UPDATE | yes — `ck_simulated_outcome_approach_b_no_rescue` |
| Control rows changed (golden) | **0** |
| Control signals differing from Day 2B baseline | **0** |
| Control failure rate, golden | 4.921% vs 4.92% baseline |

Approach A is not merely discouraged, it is **unrepresentable**: the constraint makes the row unstorable. This is the single best engineering decision in Day 3.

---

## 5. Simulated Outcome Model

The causal chain is a single funnel — `incident state → GatewayRuntimeProfile → failure probability → response family → latency regime → latency value` — reusing Day 2B's `generate_signals` rather than reimplementing it.

Measured across **12,558 simulated rows**:

| Coherence check | Count |
|---|---|
| SUCCESS with a failure response | 0 |
| FAILED with APPROVED | 0 |
| TIMEOUT response/regime mismatch | 0 |
| `outcome_changed` flag wrong | 0 |
| Approach B rescues | 0 |

Coupled movement confirmed on the affected cohort (golden): failure rate 6.438% → 20.833%, p95 latency 982 ms → 1,990 ms, infrastructure-side responses 2.05% → 7.58%, timeouts 0.30% → 2.27%. One state change moved all four.

**Slow-but-successful payments remain possible** — 516 rows are `SUCCESS` in the `ELEVATED` regime, so latency is not a perfect *positive* predictor of failure.

**Finding (P2, inherited): fast failures are impossible.**

| `simulated_latency_regime` | n | failed | failure rate |
|---|---|---|---|
| NORMAL | 1,727 | 0 | **0.000%** |
| ELEVATED | 483 | 408 | 84.47% |
| TIMEOUT | 42 | 42 | 100.00% |

`regime = NORMAL` implies SUCCESS with certainty. Real payment systems produce fast declines (an insufficient-funds response returns quickly). This is Day 2B's deferred **P2-1** carried into Day 3 unchanged, now more visible because Day 3 publishes latency evidence. It does **not** currently distort RCA — the hypothesis engine weights signal/divergence/localisation/temporal/independence and a response-mix tilt, never raw latency — but it makes latency and failure-rate detectors perfectly redundant in one direction, and a future detector that keyed on latency would inherit a shortcut.

---

## 6. Epistemic Integrity

The five layers stay distinct: observed fact (`transactions`), synthetic infrastructure attribution (Day 2B), simulated incident outcome (`simulated_*`), incident ground truth (isolated table), and RCA prediction (`incident_rca_results`).

**Static.** An independent AST scan (docstrings stripped, comments dropped by `ast.unparse`) over all 17 modules:

| Module | Touches ground truth in executable code |
|---|---|
| `detect.py`, `evidence.py`, `hypothesis.py`, `rca.py`, `metrics.py`, `statistics.py` | **clean** |
| `pipeline.py`, `handoff.py` | **clean** |
| `models.py` (schema), `incident.py` (writes), `evaluation.py` (post-hoc read), `scenarios.py`, `cli.py` | present, by design |

That `pipeline.py` is clean matters: the orchestrator never routes ground truth into diagnosis. That `handoff.py` is clean matters: Day 4's agent cannot read the answer key.

**Dynamic (adversarial).** Ground truth **deleted** → identical `rca_fingerprint`. Ground truth **corrupted to blame `gateway_A` with a wrong cause string** → RCA still returned `gateway_C`, identical fingerprint.

**Mutation.** Deliberately importing `IncidentGroundTruth` into `rca.py` was **caught** by the suite.

No hard-coded shortcuts were found in diagnosis logic. `gateway_C` appears in the package only in CLI display strings, comments, and `scenarios.py` (which defines scenarios) — never in `detect`, `evidence`, `hypothesis`, `rca`, or `metrics`.

---

## 7. Flagship Scenario

Reproduced three times, identical each run.

| Metric | Value |
|---|---|
| Window / target | gateway_C, 2024-06-01→06-04 IST, ×3.5 / ×2.2 / ×6.0 |
| Rows in window / changed | 2,093 / 36 |
| Affected cohort | 264 txns, 6.438% → **20.833%** |
| Control (A, B, D, E) | 1,829 txns, **4.921%** |
| Detection | **9.2575σ**, CRITICAL, **rank 1** of 247 cohorts |
| Latency p95 | 982 ms → 1,990 ms |
| Infrastructure-side rate | 2.05% → 7.58% |
| GMV in cohort / at risk | 324,222.00 / **70,422.00** |
| RCA | **CONFIDENT 0.6396**, "Payment gateway gateway_C is degraded" |
| Evidence | 6 supporting, 0 contradicting, 4 alternatives |
| Ground truth | matched — consulted only post-inference |

Signal landed inside the 9–13σ expectation. Realised 20.83% sits at the low edge of the 20–25% band; the report attributes this to sampling variation at n=245, and the arithmetic checks out (the multiplier for 22.5% in expectation is 3.49; 3.5 was used).

---

## 8. Generalization

**This is the section that decides whether Day 3 is a demo or a system.** Six scenarios beyond the flagship, all with ground truth consulted only after inference.

| # | Scenario | Injected | Detection | RCA verdict | RCA cause | Correct |
|---|---|---|---|---|---|---|
| A | Golden | gateway_C ×3.5 | 9.26σ CRITICAL | CONFIDENT 0.640 | gateway_C | ✅ |
| B | No incident | — | **0 anomalies** / 248 | INSUFFICIENT_EVIDENCE | *(declines)* | ✅ |
| C | **Different gateway** | gateway_B ×4.2 | 19.66σ CRITICAL | CONFIDENT 0.777 | **gateway_B** | ✅ |
| D | Issuer (HDFC) | HDFC ×4.5 | 15.73σ CRITICAL | CONFIDENT 0.847 | **HDFC**, gateway = None | ✅ |
| D′ | Issuer (SBI) | SBI ×4.5 | 17.82σ CRITICAL | CONFIDENT 0.739 | **SBI**, gateway = None | ✅ |
| E | **Mild, sub-threshold** | gateway_C ×1.3 (Δ1.9pp) | **0 anomalies** | INSUFFICIENT_EVIDENCE | *(declines)* | ✅ by design |
| F | Marginal | gateway_C ×2.0 | 5.16σ MEDIUM | CONFIDENT 0.694 | gateway_C | ✅ |
| G | Fourth gateway | gateway_D ×3.8 | 10.54σ CRITICAL | CONFIDENT 0.747 | **gateway_D** | ✅ |

**The detector is not biased toward gateway_C.** Three different gateways (B, C, D) and two different issuers (SBI, HDFC) were each correctly identified by the same code with the same thresholds. In the issuer scenarios `predicted_gateway_id` is correctly `None` and `gateway_degradation` ranks 3rd–4th carrying contradicting evidence.

**Scenario E is a deliberate true negative.** A 1.9pp deviation sits below the 2pp minimum-effect gate, so non-detection is the designed behaviour. Note that `evaluate_rca` scores this as "incorrect" because it has no notion of *correctly declined* — a measurement gap, not a system fault (P2).

**Mutation confirmation.** Forcing the gateway hypothesis to always win was caught by **three** tests, including the alternative-cause scenario. The generalisation is enforced, not incidental.

---

## 9. Anomaly Detection Quality

**Methodology.** Baseline = all pre-incident history (months, not a matched window — stable and cannot leak future information). Comparison = pooled two-proportion z. Ranking = `significance × effect_factor`, where the effect term is linear to a 15pp reference delta and clamped — so significance on a trivial move cannot outrank a real outage.

**Multiple comparisons — measured, not assumed.** Eight independent quiet 3-day windows across the calendar year:

| | Value |
|---|---|
| Cohort-tests performed | **1,994** |
| Alerts raised | **0** |
| High-severity alerts | **0** |
| Empirical FP rate | **0.00000%** |
| Expected null FPs/window at 3σ | 0.336 (before the effect gate) |
| Bonferroni-equivalent σ (FWER 0.05) | 3.54 vs 3.0 configured |

The configured 3.0σ is *below* the Bonferroni-equivalent 3.54σ, so on significance alone the design would be marginal. In practice the **2pp minimum-effect gate** does the work, and the measured false-positive count is zero across ~2,000 tests.

**Verdict: no FDR/Bonferroni correction is required for this configuration**, and this is an empirical result rather than an assertion. The honest characterisation is *"a significance test with a minimum-effect gate, empirically validated at zero false positives over 1,994 cohort-tests"* — not "multiple-comparison-corrected". The report should not claim the latter, and it does not.

**The real statistical risk is not null false positives — it is spillover** (§10).

---

## 10. Alert Fatigue / Operational Behaviour — **P1-1**

A degraded gateway drags every dimension that intersects it. Those cohorts have *genuinely* elevated rates, so no significance correction removes them; they are statistically real and causally derivative.

| Run | Incident | Alerts reported | High-severity | Alerts that are the cause | Precision |
|---|---|---|---|---|---|
| 1 | gateway_C | 6 | 1 | 1 | 16.7% |
| 3 | issuer SBI | 17 | 4 | 1 | 5.9% |
| **4** | **gateway_B** | **22** | **8** | **1** | **4.5%** |
| 5 | issuer HDFC | 11 | 1 | 1 | 9.1% |
| 7 | marginal | 2 | 0 | 1 | 50.0% |
| 8 | gateway_D | 8 | 1 | 1 | 12.5% |

**Mean precision 16.5%; worst case 1 of 22.** In Scenario C, `device=Android` was reported **CRITICAL at 9.11σ** purely for carrying gateway_B traffic, alongside `network=5G`, `network=4G`, `payment_method=P2M`, `payment_method=P2P` and `sender_bank=IndusInd` — all shadows.

**Rank 1 was the true cause in 6/6 incidents**, so ranking is sound and RCA is unaffected. The defect is confined to the alert *surface*.

**Why this is P1 rather than P2:** `handoff.build_handoff()` returns every non-suppressed anomaly as a `DetectionView`. Day 4's agent reasons over that list, and Day 5 renders it. Shipping an 8-critical-alert surface for a single gateway fault is exactly the alert fatigue a payment-ops tool exists to prevent.

**The fix already exists in the codebase.** `detect.py::_suppress_redundant` only suppresses *nested* cohorts (`broader.depth < candidate.depth` plus subset match), so two depth-1 cohorts on different dimensions can never suppress each other. Meanwhile `evidence.py` already computes an `independence` score via residual exclusion, and `hypothesis.py` already uses it to demote shadows correctly. Applying the same independence test to the reported anomaly set — suppressing a cohort whose anomaly collapses when the leading suspect is excluded — would reuse existing machinery.

**Recovery / hysteresis: MISSING, deferred.** Anomalies carry a stable logical key (`cohort_key` is identical across recomputation) but no stable identity (`anomaly_id` 73 → 84 for the same cohort) and no state. Aventum cannot presently distinguish *still active* / *recovering* / *resolved*, and would re-mint alerts on every recomputation. Day 5's Verify stage needs this. Documented as a known limitation (P2) — appropriate to defer on a five-day schedule, but it must be an explicit Day 5 deliverable rather than a surprise.

---

## 11. Evidence Engine

| Evidence type | Records | Missing source | Missing explanation |
|---|---|---|---|
| failure_rate, latency, response_mix, control_comparison, blast_radius, temporal_alignment, confounding_check, gmv_impact | 41 each | **0** | **0** |

Every subject receives all eight types — symmetric coverage, no silent gaps. The required evidence list from the review brief is fully covered: failure-rate and failure-count increase, latency increase, timeout/infrastructure-side increase, affected traffic, affected GMV, control comparison, and temporal alignment.

**Citation integrity, live:**

| Check | Dangling references |
|---|---|
| Hypothesis supporting IDs → `incident_evidence` | **0** |
| Hypothesis contradicting IDs | **0** |
| RCA supporting IDs | **0** |

Every cited `E<id>` resolves to a real row. **Mutation**: inflating a reported baseline by 0.5× was caught by `test_evidence_values_match_the_underlying_metrics_exactly` — fabricated numbers do not survive.

The `confounding_check` type deserves note as the strongest analytical idea in Day 3: it asks whether a cohort's anomaly *survives removing the leading suspect on another dimension*, which is what separates a cause from its shadow. It was added in response to a measured failure (the golden scenario was UNCERTAIN at 0.53 before it), which is the right way for a design to change.

**One honest approximation, correctly disclosed:** control latency is a volume-weighted mean of per-cohort p95s, not a pooled p95 (which cannot be recovered from per-cohort percentiles). The metric is named `latency_p95_weighted_mean` so the name itself discloses it.

---

## 12. Hypothesis Engine

Five categories are always scored — gateway, issuer, payment-method, network-segment, systemic — including those with no support, which score ~0 and record why. Weighted components (`independence` 0.30, `signal` 0.30, `divergence` 0.20, `localisation` 0.15, `temporal` 0.05, plus a bounded ±0.15 response-tilt modifier) are persisted in `score_components` for audit.

The RCA does **not** reduce to "highest failing gateway wins":

- In the issuer scenarios, `gateway_E` (D) and `gateway_A` (D′) were flagged as anomalies, yet `gateway_degradation` ranked 2nd–4th with 4–5 contradicting evidence items, and the issuer won.
- In the golden scenario, `network_segment_degradation` scored 0.583 on real spillover evidence and was demoted to 2nd by the independence test, not by a rule about gateways.
- The systemic hypothesis receives the same residual test: in Scenario D′ (SBI is ~26% of traffic and moves every gateway) it scored **0.000** because the population shift collapses once SBI is excluded.

Both supporting **and** contradicting evidence are recorded on every hypothesis. **Mutation**: deleting the alternatives from the RCA result was caught.

---

## 13. RCA Quality

Computed over the eight scenarios (six detected incidents, two correct declines):

| Metric | Value | Note |
|---|---|---|
| Top-1 root-cause accuracy | **6/6 (100%)** | across 3 gateways + 2 issuers |
| Correct declines | **2/2** | no-incident + sub-threshold |
| False positives (incident scenarios) | 0 | rank-1 always the true cause |
| False positives (quiet sweep) | **0 / 1,994 cohort-tests** | 8 windows |
| Evidence coverage | 8/8 types on every subject | 0 missing source or explanation |
| Evidence precision | 0 dangling citations | all IDs resolve |
| Uncertainty rate | 2/8 scenarios declined | both correctly |

**Sample size is small (8 scenarios).** These figures demonstrate the *mechanism* generalises across cause types and subjects; they are not a statistically strong accuracy estimate, and should not be quoted as one.

### P1-2 — Confidence is weakly calibrated against evidence strength

| Scenario | Top σ | RCA confidence |
|---|---|---|
| **Marginal gateway_C** | **5.16** | **0.6944** |
| **Golden gateway_C** | **9.26** | **0.6396** |
| gateway_D | 10.54 | 0.7473 |
| issuer HDFC | 15.73 | 0.8469 |
| issuer SBI | 17.82 | 0.7393 |
| gateway_B | 19.66 | 0.7766 |

```
Pearson  r(sigma, confidence) = +0.6306
Spearman r(sigma, confidence) = +0.6571
Inversions (weaker signal, higher confidence): 4
```

The starkest: a **5.16σ MEDIUM-severity** incident scored **higher** confidence than the **9.26σ CRITICAL** flagship.

**Cause.** `confidence = top_score × (0.5 + 0.5 × margin)` where `margin = (top − second)/top`. Four of the five score components (`independence`, `divergence`, `localisation`, `temporal`) measure *how cleanly one hypothesis wins*; only `signal` (weight 0.30, saturating at 12σ) measures *how strong the evidence is*. A cleanly-attributed weak signal with no rivals therefore outscores a strong signal that has plausible competitors.

That is a defensible definition of "confidence in the attribution" — but it is **not** what a downstream consumer will read it as. `5_DAY_EXECUTION_PLAN.md` specifies that Day 4's recommendation carries "bounded traffic percentage… confidence, risk", and a deterministic policy engine bounds actions. If action magnitude scales with confidence, this ordering authorises a **larger** intervention on a **weaker** incident. That is why it is P1 and not P2.

**Not a rewrite.** Either publish severity/σ alongside confidence in the handoff so Day 4 can gate on both (they are already stored), or fold an absolute-strength term into the confidence expression.

---

## 14. Uncertainty

The system can decline, and does.

| Case | Verdict | `predicted_root_cause` | Confidence |
|---|---|---|---|
| No incident | INSUFFICIENT_EVIDENCE | NULL | 0.0000 |
| Sub-threshold (Δ1.9pp) | INSUFFICIENT_EVIDENCE | NULL | 0.0263 |

A CHECK constraint makes a named cause and a declining verdict mutually exclusive, so "confidently wrong" is structurally harder than "honestly uncertain". Verdict bands are CONFIDENT ≥0.60, UNCERTAIN ≥0.35, INSUFFICIENT below.

**Gap:** the UNCERTAIN band was not exercised by any scenario — every detected incident landed CONFIDENT and every non-detection landed INSUFFICIENT. The intermediate state is implemented and unit-tested but has no end-to-end demonstration. Given P1-2 (confidence inflated by decisiveness), the practical width of the UNCERTAIN band is unproven. P2 — worth one scenario before Day 5.

---

## 15. Business Impact

GMV is computed from `transactions.amount` — authoritative observed values — inside the same aggregate as the metrics:

```sql
sum(amount)                                          AS gmv_total
sum(amount) FILTER (WHERE effective_status='FAILED') AS gmv_at_risk
```

Golden scenario: **324,222.00** total in cohort, **70,422.00 at risk**. Also available: affected transactions (264), affected traffic share, failure counts, and blast radius (fraction of the dimension affected).

Language discipline is correct throughout: the field is `gmv_at_risk`, never "recovered". The GMV evidence record carries `source_layer = OBSERVED` with an explanation stating that amounts are observed while *which* transactions failed is modelled — the precise distinction that keeps the claim honest. Eleven of 88 evidence rows are OBSERVED, 77 SIMULATED; the layers are never flattened.

---

## 16. Idempotency / Concurrency / Crash Safety

| Test | Result |
|---|---|
| 5× sequential incident+simulation | 1 incident, 1 run, 2,093 outcomes, identical fingerprint |
| 2× full pipeline, same definition | identical analysis + RCA fingerprints |
| Analysis runs accumulate | 2 rows — by design, each is an audit record |
| Interrupted pipeline (exception pre-commit) | 0 rows committed, 0 orphans |
| 4-thread barrier-synchronised race | 1 incident, 0 errors, 1 row |
| Duplicate evidence / RCA | none — scoped per analysis run |

Simulation is idempotent *by replacement*: prior rows are removed before re-insert. A mutation removing the explicit delete **survived** — because `fk_simulated_outcome_run ON DELETE CASCADE` already removes them when the prior run row is deleted. The explicit delete is therefore redundant defensive code, not a bug (P2, harmless).

---

## 17. Provenance

```
simulated outcome → incident → generation run → source ingestion run → dataset registry → SHA-256
```

| Link | Rows resolving |
|---|---|
| → `transactions` | 14,651 / 14,651 |
| → `ingestion_runs` | 14,651 / 14,651 |
| → `synthetic_generation_runs` | 14,651 / 14,651 |
| → `dataset_registry` | 14,651 / 14,651 |

All resolve to `upi_transactions_2024` / `8e46a45f…c89b6`. **Zero broken links.**

Provenance survives transformation: evidence rows carry `source_layer` and `evidence_source`; anomalies carry cohort definition and window; the handoff stamps `SYNTHETIC_INCIDENT` and `SIMULATED_INCIDENT_OUTCOME`. All eight Day 3 tables carry machine-enforced provenance flags, each adversarially tested against a clearing UPDATE with rows present.

---

## 18. Reproducibility

| Property | Result |
|---|---|
| Re-simulation | identical fingerprint (5 repeats) |
| Full pipeline re-run | identical analysis **and** RCA fingerprints |
| Seed change | fingerprint changes |
| Multiplier change | fingerprint changes |
| Scenario re-runs after truncate/reorder | identical |
| Determinism source | SHA-256 only; `LANE_*` slicing; no `hash()`, no unseeded RNG, no wall-clock, no row-order dependence |

Fingerprints deliberately exclude surrogate IDs and timestamps, so a clean rebuild reproduces them despite different primary keys.

**One real defect found and already corrected during implementation:** the `scenarios` CLI command passed a single shared `--seed` to all three sub-scenarios, so the alternative scenario silently ran under the golden seed. Determinism was never broken — the input genuinely differed — but the CLI misrepresented which configuration was executing. Verified fixed: `alternative` now produces `ef145749…` identically whether run standalone or via `scenarios`.

---

## 19. Security / Data Safety

| Check | Result |
|---|---|
| Day 3 writes to `transactions` | none |
| Ground truth reachable from diagnosis | no (static + adversarial) |
| Ground truth in Day 4 handoff | no (payload scanned) |
| SQL injection surface | closed — interpolation is column names from the `DIMENSION_SQL` allow-list, guarded by `if dimension not in DIMENSION_SQL: raise`; all values are bound parameters |
| Secrets in code | none |
| Destructive statements | none; deletes are scoped ORM `delete()` on Day 3 tables only |
| `baseline-v1` mutation | none — values identical pre/post |

---

## 20. Production Substitution Readiness

**This is the strongest architectural result in the review.**

Modules referencing synthetic/simulated *table names*:

| Module | Refs | Role |
|---|---|---|
| `metrics.py` | 3 | the `_EFFECTIVE_CTE` — **the adapter boundary** |
| `simulate.py` | 2 | the generator (replaced wholesale by real telemetry) |
| `models.py` | 7 | schema definitions |
| `scenarios.py`, `__init__.py` | 1 each | configuration / docs |

Modules referencing them: **`detect.py` 0, `evidence.py` 0, `hypothesis.py` 0, `rca.py` 0, `statistics.py` 0.**

The entire intelligence layer depends only on `CohortMetrics`, `AnomalyCandidate`, `DetectionResult`, `EvidenceBundle` — plain dataclasses. Dependency graph is **acyclic with zero upward-dependency violations**.

| Question | Answer |
|---|---|
| Which interfaces remain unchanged? | Everything from `CohortMetrics` upward: detection, evidence, hypothesis, RCA, handoff |
| What would be replaced? | `simulate.py` (generation) and the `_EFFECTIVE_CTE` join in `metrics.py` |
| Which components stay reusable? | All of `detect` / `evidence` / `hypothesis` / `rca` / `statistics` — unchanged |
| Which schema is synthetic-only? | `simulated_incident_outcomes`, and the join to `synthetic_infrastructure_assignments` |
| Can the source be swapped without rewriting intelligence? | **Yes** — the coupling is one CTE in one module |

**Gap (P2):** the boundary is narrow but *informal*. There is no `Protocol`/ABC for a telemetry source, so substitution means editing a query rather than registering an implementation. The desired direction in the brief is already structurally satisfied; formalising it before Day 5 would make it explicit rather than emergent.

---

## 21. Day 4 Handoff

`build_handoff(session, analysis_run_id)` returns five typed objects; `ranked_hypotheses()` returns the full ranked set. Verified by emitting live JSON.

| Required field group | Present |
|---|---|
| Detection: `anomaly_id · severity · anomaly_score · significance_sigma · cohort_key · affected_population · baseline_metrics · current_metrics · detection_window · gmv_at_risk · rank` | ✅ all |
| Evidence: `evidence_id · evidence_type · metric · baseline · current · delta · significance_sigma · cohort · control · source_layer · evidence_source · explanation` | ✅ all |
| RCA: `incident_id · verdict · predicted_root_cause · predicted_hypothesis_type · predicted_gateway_id · predicted_segment · confidence · summary · explanation · supporting_evidence_ids · contradicting_evidence_ids · alternatives_considered · affected_population · control_population · rca_fingerprint` | ✅ all |

Day 4 need not touch raw tables. Ground truth is absent from the payload (verified by scanning the serialised output).

**Sufficiency for the Day 4 simulator:** the handoff exposes per-cohort baseline and current metrics, control-group aggregates, blast radius, and GMV at risk — enough to project a reroute. What it does *not* expose is per-gateway spare capacity or routing eligibility, which Day 4's counterfactual simulator will need to read from Day 2B's `synthetic_routing_policies` / `synthetic_gateway_profiles` directly. That is a reasonable boundary but should be a conscious Day 4 decision, not a discovery.

**Two additions worth making now** (both cheap, both tied to P1s): expose `severity`/σ alongside `confidence` at the RCA level so Day 4 can gate on evidence strength, and mark shadow alerts so Day 4's agent is not handed 22 equal-looking detections.

---

## 22. Code Architecture

4,835 lines across 17 modules; largest is `models.py` at 721 lines (schema, appropriately).

| Property | Result |
|---|---|
| Dependency cycles | **none** |
| Upward-dependency violations | **none** |
| Separation | incident / simulation / metrics / detection / evidence / hypothesis / RCA / persistence / CLI all distinct |
| Shared mutable state | none — `MetricStore` is per-analysis and passed explicitly |
| Hard-coded scenario knowledge in diagnosis | none |
| Testability | pure functions in `statistics.py`, `rng.py`; DB-free unit tests exist |

`MetricStore` memoisation is a genuine design win: detection and evidence share one aggregate set, so the same number can never be reported two different ways by two code paths.

---

## 23. Test Quality

**Test count is not the metric.** Mutation testing against an isolated copy of the source tree (the repository was never modified):

| # | Mutation | Full-suite result | Assessment |
|---|---|---|---|
| M1b | Force gateway hypothesis to always win | **CAUGHT** (3 tests) | anti-overfit protection is real |
| M2 | Leak ground truth into `rca.py` | **CAUGHT** | isolation is enforced |
| M3 | Fabricate evidence baseline (×0.5) | **CAUGHT** | numbers can't be invented |
| M4 | Mislabel every row as affected (kill controls) | **CAUGHT** (6 tests) | control integrity enforced |
| M5 | Drop RCA alternatives | **CAUGHT** | alternatives are required |
| M6 | Break incident idempotency | **CAUGHT** | idempotency enforced |
| M7 | Skip explicit outcome delete | SURVIVED | **inert** — FK cascade preserves behaviour |
| M8 | Remove minimum-cohort-size guard | SURVIVED | **inert on this data** — smallest reported cohort is 117; no cohort <100 clears the other gates |

**Effective score 6/8, with both survivors confirmed inert rather than test weaknesses.** M7 changes no behaviour; M8's guard is a safety net this dataset never exercises.

Round 1 (running only the single "matching" test per mutation) scored 4/8, and the difference is instructive: `test_control_cohort_is_exactly_unchanged` passes *vacuously* under M4, because if no row is labelled a control, the "no control changed" query returns zero. Six other tests catch it, so the suite is sound — but that individual test would be stronger asserting `control_population > 0` first. Likewise `test_small_cohorts_are_not_scored` asserts a property that holds with or without the guard on this data, so it provides no protection. Both P2.

---

## P0 Issues

**None.**

---

## P1 Issues

### P1-1 — Alert surface is dominated by causally derivative alerts

*Evidence:* Scenario C emitted 22 alerts (8 CRITICAL/HIGH) for one gateway fault; only rank 1 was the cause. Mean precision 16.5% across six incidents, worst 4.5%. `device=Android` reported CRITICAL at 9.11σ purely for carrying gateway_B traffic.

*Risk:* `handoff.build_handoff()` publishes every non-suppressed anomaly. Day 4's agent reasons over that list; Day 5 renders it. This is the alert fatigue the product exists to prevent, shipped as the product's own output.

*Why not P2:* it is the Day 4 input surface, and the fix window closes once Day 4 builds against it.

*Required action:* apply the existing independence/confounding test to alert suppression (a cohort whose anomaly collapses when the leading suspect is excluded is a shadow), or flag such alerts as derivative in `DetectionView`. Machinery already exists in `evidence.py`/`hypothesis.py`.

### P1-2 — RCA confidence is weakly calibrated against evidence strength

*Evidence:* Pearson r = +0.63, 4 inversions. A 5.16σ MEDIUM incident scored 0.6944; the 9.26σ CRITICAL flagship scored 0.6396.

*Risk:* Day 4 bounds recommended actions by confidence. This ordering authorises a larger intervention on weaker evidence.

*Required action:* surface severity/σ alongside confidence in the RCA handoff so Day 4 can gate on both (values already stored), or incorporate an absolute-strength term into confidence. Then exercise the UNCERTAIN band end-to-end.

---

## P2 Issues

| # | Issue | Evidence | Why deferrable |
|---|---|---|---|
| P2-1 | No alert identity or lifecycle (active/recovering/resolved); recomputation re-mints `anomaly_id` | 73 → 84 for same `cohort_key` | Day 5 Verify requirement; correctly deferred, but must be an explicit Day 5 deliverable |
| P2-2 | Fast failures impossible — NORMAL regime 0.000% failure rate | 1,727 rows, 0 failures | Inherited Day 2B P2-1; does not distort current RCA |
| P2-3 | No formal telemetry-source adapter (Protocol/ABC) | coupling is 1 CTE in `metrics.py` | Substitution already works; formalising is polish |
| P2-4 | No advisory lock on `create_incident` | 4-thread race passed; UNIQUE guarantees integrity | Same class as Day 2A P2 |
| P2-5 | Redundant explicit outcome delete masked by FK cascade | M7 inert | Harmless defensive code |
| P2-6 | `test_control_cohort_is_exactly_unchanged` passes vacuously under M4 | 6 other tests catch it | Suite is sound; individual test weak |
| P2-7 | `test_small_cohorts_are_not_scored` is tautological on this data | M8 inert; min reported 117 | Guard is correct; test proves nothing |
| P2-8 | `evaluate_rca` has no "correctly declined" outcome | Scenario E scored incorrect despite correct behaviour | Metric gap, not system fault |
| P2-9 | UNCERTAIN verdict band never exercised end-to-end | all scenarios CONFIDENT or INSUFFICIENT | One scenario would close it |
| P2-10 | Control latency is a weighted mean of p95s, not pooled p95 | disclosed in the metric name | Correctly disclosed |

---

## Evidence Table

| Area | Status | Severity | Direct Evidence | Risk | Required Action |
|---|---|---|---|---|---|
| Day 2 regression | PASS | — | 362/362, content MD5 `13965d76…` identical pre/post | none | none |
| Incident | PASS | — | 5 repeats → 1 row; 4-thread race → 1 row, 0 errors | none | none |
| Approach B | PASS | — | 0 rescues / 14,651; DB rejects coherent rescue; controls exactly 0 changed | none | none |
| Simulation | PASS | P2 | 0 impossible combos / 12,558; NORMAL regime 0% failures | realism gap | defer (Day 2B P2-1) |
| Epistemic integrity | PASS | — | AST clean on 5 diagnosis modules + pipeline + handoff; deleted & corrupted GT → identical fingerprint | none | none |
| Flagship | PASS | — | 9.2575σ, 20.833% vs control 4.921%, CONFIDENT 0.6396 | none | none |
| Generalization | PASS | — | 6/6 top-1 across gateways B/C/D + issuers SBI/HDFC; 2/2 correct declines | none | none |
| Detection | PASS | — | 247 cohorts, rank-1 correct 6/6 | none | none |
| Statistical validity | PASS | — | **0 FP / 1,994 cohort-tests**, 8 quiet windows; Bonferroni-equiv 3.54σ vs 3.0 configured | low | document; no correction needed |
| Alert behaviour | **FAIL** | **P1-1** | 22 alerts / 8 high-sev for 1 fault; mean precision 16.5% | alert fatigue in Day 4/5 surface | apply independence test to suppression |
| Evidence | PASS | — | 8 types × 41, 0 missing source; **0 dangling citations** | none | none |
| RCA | PASS | — | 6/6 correct, cites real IDs, lists 4 alternatives | none | none |
| Uncertainty | PARTIAL | P2-9 | declines correctly 2/2; UNCERTAIN band unexercised | unproven band | one scenario |
| Business impact | PASS | — | GMV 324,222 / at-risk 70,422 from observed amounts; layers labelled | none | none |
| Idempotency | PASS | — | identical fingerprints; crash → 0 rows, 0 orphans | none | none |
| Provenance | PASS | — | 14,651/14,651 to SHA-256 `8e46a45f…` | none | none |
| Reproducibility | PASS | — | identical fingerprints across repeats/truncate/reorder | none | none |
| Security | PASS | — | no writes to observed; allow-listed columns + bound params; no secrets | none | none |
| Production substitution | PASS | P2-3 | intelligence layer: **0** synthetic table refs; acyclic graph | none | formalise adapter |
| Day 4 handoff | PASS | P1-2 | all required fields present; GT absent | confidence gating | expose severity/σ |
| Code architecture | PASS | — | acyclic, 0 layering violations, no shared mutable state | none | none |
| Test quality | PASS | P2-6/7 | **6/8 mutations caught**; both survivors inert | 2 weak tests | strengthen assertions |
| **Final verdict** | **APPROVED WITH REQUIRED FIXES** | **0 P0 / 2 P1 / 10 P2** | — | — | fix P1-1, P1-2 before Day 4 |

---

## Five-Day Continuity

Day 3 consumed no Day 4 or Day 5 scope. Verified absent: Qwen/Ollama, counterfactual simulator, recommendation, approval, execution, rollback, frontend, WebSockets. `frontend/`, `simulator/`, `agent/` are empty. The two P1 fixes are narrow — one reuses existing machinery in `detect.py`, the other exposes fields already stored — and neither justifies schedule expansion.

---

## Final Decision

# APPROVED WITH REQUIRED FIXES

Day 3 is architecturally sound and intellectually honest. It generalises across cause types and subjects, refuses to answer when evidence is thin, cannot see its own answer key, cannot corrupt observed history, and cannot fabricate evidence — each verified adversarially rather than asserted. The production-substitution property is real: the intelligence layer has zero coupling to synthetic sources.

Two P1 issues must be fixed before Day 4 builds against these interfaces:

1. **P1-1** — suppress or flag causally derivative alerts using the independence test that already exists.
2. **P1-2** — expose severity/σ alongside confidence, or fold absolute strength into confidence, so bounded actions scale with evidence strength.

The ten P2 items may be deferred. P2-1 (alert lifecycle/hysteresis) should be an explicit Day 5 deliverable rather than an assumption.

---

_Review method: full suite executed; 8 additional scenarios injected and analysed; 8-window false-positive sweep (1,994 cohort-tests); 8 mutation experiments against an isolated source copy; AST-based static isolation scan; 4-thread concurrency race; crash-interruption probe; live schema and provenance queries. Review harnesses were written to a scratchpad outside the repository. No production code, schema, migration, or test was modified. Day 3 incident rows were created as normal operation of the system under review; observed data was verified byte-identical before and after._
