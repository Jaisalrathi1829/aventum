_Aventum internal review document — independent verification gate._

# Day 2B Architecture Review

Independent review of the synthetic payment-infrastructure layer. Every conclusion is backed by the live database, the current code, or an executed command — not by the Day 2B report, which is treated as a claim to be checked.

---

## Executive Verdict

# APPROVED WITH REQUIRED FIXES

Day 2B is well-built. The synthetic layer is mathematically sound, genuinely deterministic, fully provenance-enforced, and does not touch canonical data. All 260 tests pass, both fingerprints reproduce exactly, and every claimed number in the Day 2B report that I re-measured was accurate.

**One P1 must be settled before Day 2C**, and it is architectural rather than a defect: the status-conditioned design means an incident injected by *reallocating* observed failures would make the control group appear **twice as healthy** during the incident (measured: 0.48× baseline at a 25% degradation). That artifact would bias any RCA evaluation optimistically. The review's Section 16 asks for this decision; the answer is **adopt Approach B** (generate a synthetic incident-outcome layer), documented in [DAY2C_INTERFACE_READINESS.md](DAY2C_INTERFACE_READINESS.md).

No P0 issues. Six P2 items, all deferrable.

## Test Results

| Metric | Value |
|---|---|
| Total | **260** |
| Passed | **260** |
| Failed | 0 |
| Skipped | 0 |
| Runtime | 155.93 s |

Per-file: `test_normalize` 41 · `test_validate` 53 · `test_db_constraints` 35 · `test_pipeline` 26 · `test_regression_full_source` 18 · `test_dataset_provenance` 25 (= 198 Day 2A) · `test_synthetic_infrastructure` 62 (Day 2B).

No `skip`/`xfail` markers beyond two environment guards (missing source file, unreachable PostgreSQL). No Day 2A test was removed or weakened — the 198 figure matches the post-P1-fix baseline exactly.

## Database / Schema

Migration chain verified: `base → 0001 → 0002 → 0003 (head)`.

| Table | PK | FK | UNIQUE | CHECK | NOT NULL | Indexes | Rows |
|---|---|---|---|---|---|---|---|
| `synthetic_generation_runs` | 1 | 1 | 0 | 3 | 12 | 3 | 1 |
| `synthetic_gateways` | 1 | 0 | 0 | 2 | 5 | 1 | 5 |
| `synthetic_gateway_profiles` | 1 | 1 | 1 | 4 | 10 | 3 | 5 |
| `synthetic_routing_policies` | 1 | 0 | 0 | 2 | 7 | 1 | 1 |
| `synthetic_routing_policy_gateways` | 1 | 2 | 1 | 2 | 6 | 2 | 5 |
| `synthetic_gateway_health_states` | 1 | 2 | 0 | 6 | 10 | 3 | 5 |
| `synthetic_infrastructure_assignments` | 1 | 5 | 1 | 9 | 18 | 7 | 250,000 |

Schema matches the documentation. Not unnecessarily complex: seven tables for gateway universe, versioned behaviour, versioned policy, eligibility, time-bounded health, per-transaction output, and run audit — each with a distinct responsibility. **Ready for Day 2C without migration** (see Interface Readiness §3).

## Provenance

**Verified, not assumed:**

- `transactions` has exactly its 16 original columns. No synthetic column was added.
- **Adversarial test:** `UPDATE <table> SET is_synthetic = false` was attempted against all seven synthetic tables. **All seven were rejected by PostgreSQL** with a check-constraint violation.
- Full lineage resolves end-to-end: synthetic row → `generation_run_id` → `source_ingestion_run_id` → `source_filename` → `source_sha256` (`8e46a45f…`). Broken-lineage rows: **0**. Rows whose `source_ingestion_run_id` disagrees with the transaction's `ingestion_run_id`: **0**.
- Read surface `v_transaction_infrastructure`: 14 `observed_*`, 13 `synthetic_*`, plus `transaction_provenance='OBSERVED'`, `infrastructure_provenance='SYNTHETIC'`, `infrastructure_is_synthetic=true`. The only unprefixed columns are `transaction_id` and the three provenance markers themselves.

## Staleness

Tested with a controlled mismatch inside a rolled-back transaction. Notably, the FK on `source_ingestion_run_id` had to be dropped before a mismatch could even be *written* — a stronger guarantee than the staleness check alone.

With the mismatch in place (`generation.source_ingest=999` vs `canonical=1`) the mismatch was detected. State returned to `CURRENT` after rollback. Stale data cannot masquerade as current: `assess_staleness()` compares run→canonical ingestion identity *and* coverage count, and the `ON DELETE CASCADE` on `transaction_id` means a re-ingestion wipes the population rather than orphaning it.

## Reproducibility

| Fingerprint | Value | Status |
|---|---|---|
| Canonical (Day 2A) | `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8` | reproduced |
| Generation (Day 2B) | `e8414edd5a58c6cf04876e1bf48ca9a5564cf8d77da8eca4201c1732f52fe3c8` | **reproduced independently in this review** |

A fresh generation run executed during this review produced the identical generation fingerprint, identical gateway distribution, identical failure rates, identical latency summary, and 250,000 rows. Seed sensitivity is covered by `test_changing_the_seed_changes_the_fingerprint`; regeneration equality by `test_regeneration_with_same_seed_reproduces_the_fingerprint`. No expected fingerprint was modified.

## Deterministic RNG

Genuinely deterministic, not accidentally so — verified by running the same draw in **five separate processes** under `PYTHONHASHSEED` ∈ {0, 1, 42, 12345, random}:

```
ed55a461548909c8 | gateway_E | 228.086005 | 0.927088044894   (identical × 5)
```

Static scan confirms: no salted `hash()`, no `random`/`numpy.random`, no global mutable state. Wall-clock appears only in audit metadata (`finished_at`, `duration_seconds`) — never as a generation input. Row order does not participate: each row's values derive solely from `sha256(transaction_id|ingestion_run|config_version|seed)` sliced into four disjoint 64-bit lanes. `ORDER BY transaction_id` on the read makes the COPY payload stable, but the values themselves are order-independent.

Lane 3 (`LANE_RESERVED`) is unused and available for Day 2C.

## Calibration Integrity

Code re-derived and compared against [DAY2B_CALIBRATION_SPEC.md](DAY2B_CALIBRATION_SPEC.md). **Every documented value matches the implementation.**

| Gateway | Rail | Ref fail% | Raw ratio | Damped | Abs prob% | Weight |
|---|---|---|---|---|---|---|
| gateway_A | rail_A | 1.85 | 0.690 | 0.814 | 4.020 | 0.26 |
| gateway_D | rail_D | 2.39 | 0.891 | 0.935 | 4.616 | 0.21 |
| gateway_B | rail_B | 2.85 | 1.063 | 1.038 | 5.125 | 0.27 |
| gateway_E | rail_E | 3.40 | 1.268 | 1.161 | 5.732 | 0.13 |
| gateway_C | rail_C | 3.83 | 1.428 | 1.257 | 6.208 | 0.13 |

- Reference weighted mean **2.6819%** (doc claims 2.68%) ✓
- Damping λ = **0.6** ✓ · spread ratio **1.5443×** (doc claims 1.54×) ✓
- Weights sum to **1.000000** ✓
- **Weighted-average absolute failure probability = 4.950400%, exactly the observed canonical rate** ✓
- All five response mixes sum to 1.000000 ✓

The three categories are cleanly separated in code: `REFERENCE_*` constants (measurements about the reference dataset), derived Aventum parameters, and the observed UPI rate read from `transactions` at runtime. The absolute failure *level* comes from observed data, never from the reference — only the relative *shape* is transferred. That is the correct boundary.

## Status-Conditioned Attribution Model

The most important review item. Verified both analytically and empirically.

**The identity.** Because weights sum to 1 and calibration normalises `Σ(w_g·p_g) = f` (the observed failure rate):

```
E[share_g]    = f·(w_g p_g / f) + (1−f)·(w_g(1−p_g)/(1−f)) = w_g p_g + w_g(1−p_g) = w_g    EXACT
E[failrate_g] = (N·w_g·p_g) / (N·w_g) = p_g                                                EXACT
```

Confirmed numerically: `Σ(w·p) = 0.04950400` vs observed `f = 0.04950400`.

**Empirical (250,000 rows):**

| Gateway | Share | Target | Dev | Failure rate | Target | Dev |
|---|---|---|---|---|---|---|
| gateway_A | 0.26058 | 0.26 | +0.00058 | 0.03937 | 0.04020 | −0.00082 |
| gateway_B | 0.26946 | 0.27 | −0.00054 | 0.05157 | 0.05125 | +0.00032 |
| gateway_C | 0.13076 | 0.13 | +0.00076 | 0.06421 | 0.06208 | +0.00213 |
| gateway_D | 0.21039 | 0.21 | +0.00039 | 0.04677 | 0.04616 | +0.00061 |
| gateway_E | 0.12881 | 0.13 | −0.00119 | 0.05521 | 0.05732 | −0.00211 |

**Marginal preservation is exact:** observed failures 12,376 = sum over gateways 12,376.

`modeled_failure_probability` is persisted per row and is distinct from observed status — it is the forward model's belief about a `(gateway, context, health)` combination, which Day 2C's simulator will need.

### Direct answers

**A. Is this a statistically legitimate attribution/calibration construction?**
**Yes.** It is Bayesian posterior sampling from the forward model the profiles describe, mathematically equivalent to forward-generating outcomes and retaining only draws consistent with observed status. Exact in expectation, and empirically within 4σ on every gateway.

**B. Is "a plausible synthetic infrastructure world consistent with the observed outcomes" a legitimate description?**
**Yes** — and it is the strongest claim the construction supports. The code and docs use exactly this register.

**C. Could a downstream RCA model confuse this with real causal evidence?**
**Not through the intended surfaces.** Four enforcement mechanisms were independently verified. The residual risk is a future tool author writing a bespoke query that strips `synthetic_` prefixes — a Day 2C+ obligation, already recorded as a known limitation rather than claimed solved.

**D. Does this introduce circularity that could make RCA look stronger than it really is?**
**Partially, in two distinct forms — and the distinction matters.**

- *Benign (standard practice):* injecting a known cause and checking the detector recovers it is how synthetic evaluation works. Not damaging, provided ground truth stays out of the diagnosis path — which the truth model forbids and the schema supports.
- *Non-benign, and specific to reallocation:* because total failures per window are fixed by observed data, concentrating failures onto a degrading gateway **necessarily depletes them from the control group**. Measured on a real 3-day window, driving gateway_C to 25% drops the other four gateways to **0.48× their baseline rate**. An RCA engine would face an artificially easy contrast. **This is the P1** and drives the Approach B recommendation.

## Generative Model Coherence

All impossible combinations measured at **zero** across 250,000 rows:

| Check | Count |
|---|---|
| SUCCESS + failure response | 0 |
| FAILED + APPROVED | 0 |
| TIMEOUT response ↔ non-TIMEOUT regime | 0 |
| APPROVED + non-approved attribution | 0 |
| NORMAL regime above 1,800 ms cap | 0 |
| TIMEOUT regime below 2,000 ms floor | 0 |
| **[intentionally allowed] APPROVED + ELEVATED** | **9,395** |

The deliberately-permitted slow-success case is present at 3.95% of successes, confirming the earlier over-constraint was correctly removed. Four of these invariants are enforced by database CHECK constraints, not merely by the generator — so an ETL bug cannot persist an incoherent row.

## Health Model

A genuine model state, not an arbitrary label. Time-bounded (`valid_from`/`valid_to`, CHECK `valid_to > valid_from`), bound to a generation run with CASCADE, and parameterised by three multipliers.

Baseline verified: 5 windows, one per gateway, all `HEALTHY`, all multipliers `1.0`, and **all 250,000 assignments `HEALTHY`**. No hidden degradation.

The Day 2C code path was inspected: all three multipliers flow through `GatewayRuntimeProfile.effective_failure_probability`, `.effective_latency_multiplier`, and `.effective_response_mix()`. A `DEGRADED` window therefore moves failure probability, latency, and response mix **coherently from one state change** — no independent mutation of each signal. Covered by `test_health_degradation_raises_modeled_failure_probability` and `test_health_degradation_shifts_the_response_mix_toward_infrastructure`.

## Gateway Baseline

| Gateway | Volume | Share | Failure rate | Timeout% of fails | Infra-side% of fails |
|---|---|---|---|---|---|
| gateway_A | 65,145 | 26.06% | 3.937% | 1.48% | 23.27% |
| gateway_B | 67,365 | 26.95% | 5.157% | 2.59% | 27.29% |
| gateway_C | 32,691 | 13.08% | 6.421% | 3.43% | 31.59% |
| gateway_D | 52,597 | 21.04% | 4.677% | 2.52% | 24.96% |
| gateway_E | 32,202 | 12.88% | 5.521% | 3.37% | 30.26% |

Differentiation is meaningful (1.63× observed best-to-worst) without any gateway resembling an incident. No gateway dominates (max 26.95%) and none is unusable (min 32,202 transactions — ample for daily analysis). All values sit within the documented calibration ranges; **no discrepancy found**.

## Routing Policy

`baseline-v1`, `selection_method = synthetic_deterministic_hash_weighted_status_conditioned`.

- **Can RCA explain which policy version was active?** Yes — `routing_policy_version` on every assignment, FK to the policy table.
- **Can the simulator evaluate an alternative policy?** Yes — policy and eligibility are data (`synthetic_routing_policy_gateways` with a nullable `eligibility_conditions` jsonb), so a new `policy_version` can be added without code change.
- **Can candidates be distinguished from the selection?** Yes — `eligible_gateways` (per row) vs `selected_gateway_id`, with the full reasoned snapshot on the run's `model_parameters`.
- **Sufficient for Day 2C?** Yes.
- **Anything falsely presented as production logic?** No. The policy description explicitly states it is a modelling construct and not any real processor's algorithm.

## Latency Model

| Regime | n | min | p50 | p95 | p99 | max | sd |
|---|---|---|---|---|---|---|---|
| NORMAL | 228,229 | 99.0 | 421.5 | 717.4 | 892.6 | 1,800.0 | 147.2 |
| ELEVATED | 21,449 | 277.4 | 868.7 | 1,428.5 | 1,748.2 | 1,990.0 | 278.8 |
| TIMEOUT | 322 | 2,000.0 | 3,344.0 | 4,985.1 | 6,228.5 | 6,846.6 | 881.4 |

Right-skewed (p50 < mean in each regime), regimes cleanly separated with no cap/floor violations, timeouts appropriately rare at 0.129% of all traffic. Gateway latency differentiation (≤8% multipliers) is not exaggerated.

**Latency is not a perfect proxy for failure — but the asymmetry is worth noting.** 3.95% of successes are `ELEVATED` and 2.70% exceed 900 ms, so a slow transaction is not necessarily a failed one. However **no failure is ever in the `NORMAL` regime**, so `latency_regime = NORMAL` implies SUCCESS with certainty. See P2-1.

## Response / Error Model

Taxonomy documented, explicitly synthetic, explicitly not real production codes, with a documented attribution mapping.

| Response | Attribution | Count | % of all |
|---|---|---|---|
| APPROVED | approved | 237,624 | 95.050% |
| INSUFFICIENT_FUNDS | issuer_side | 3,060 | 1.224% |
| PROCESSING_ERROR | infrastructure_side | 3,038 | 1.215% |
| ISSUER_DECLINED | issuer_side | 3,026 | 1.210% |
| DO_NOT_HONOR | issuer_side | 2,930 | 1.172% |
| TIMEOUT | infrastructure_side | 322 | 0.129% |

`APPROVED` count equals observed `SUCCESS` exactly (237,624).

**No artificial RCA shortcut.** Infrastructure-side attribution ranges 23.27% (gateway_A) → 31.59% (gateway_C), a 1.36× spread. Seeing a `PROCESSING_ERROR` does **not** identify the gateway — RCA must aggregate across a cohort rather than read a single row, which is the desired difficulty. The `timeout_multiplier` hook gives Day 2C a controlled way to shift this toward infrastructure-side under degradation.

## Flagship Cohort Readiness

Recomputed independently.

| Cohort level | Cells | Largest | Smallest |
|---|---|---|---|
| gateway | 5 | gateway_B 67,365 (185.1/day) | 32,202 |
| gateway × bank | 40 | gateway_B × SBI 16,893 (46.4/day) | 2,546 |
| gateway × method | 20 | gateway_B × P2P 30,305 (83.3/day) | 1,592 |
| gateway × bank × method | 160 | gateway_B × SBI × P2P 7,467 (20.5/day) | 120 |

3σ detection thresholds by window:

| Gateway | n/day | Base | 1-day | 3-day | 7-day |
|---|---|---|---|---|---|
| gateway_C | 89.8 | 6.42% | 14.2% | **10.9%** | 9.4% |
| gateway_E | 88.5 | 5.52% | 12.8% | 9.7% | 8.3% |
| gateway_B | 185.1 | 5.16% | 10.0% | 8.0% | 7.0% |
| gateway_D | 144.5 | 4.68% | 9.9% | 7.7% | 6.7% |
| gateway_A | 179.0 | 3.94% | 8.3% | 6.5% | 5.6% |

**The Day 2B report's recommendation stands: gateway_C, 3-day window, degraded to 20–25% → ≈9–13σ.** Full rationale and the control group in [DAY2C_INTERFACE_READINESS.md](DAY2C_INTERFACE_READINESS.md) §6.

## Observed-vs-Synthetic Truth Model

The five-layer separation holds. Incident ground truth does not yet exist, so it cannot leak — and the schema keeps it in Day 2C-owned tables referencing gateways and time windows, not transactions, which structurally discourages joining it into evidence.

Boundary-violation search found **one** residual path, already documented as a Day 2B known limitation: a future tool could construct its own query that strips `synthetic_` prefixes before handing rows to an LLM. The four enforcement mechanisms protect the intended read surface but cannot protect a bespoke query. Recorded as a Day 2C+ tool-design obligation.

Calibration references are never treated as transaction evidence: they appear only as names/parameters on synthetic config rows, and no reference table exists in the database.

## Day 2C Outcome Architecture Decision

**Recommendation: Approach B — generate a synthetic incident-outcome layer.** Full comparison table and the measured control-group artifact in [DAY2C_INTERFACE_READINESS.md](DAY2C_INTERFACE_READINESS.md) §5. Summary: Approach A is simpler but produces a control group that improves during the incident (0.48× baseline at 25%), which is physically implausible and biases RCA evaluation optimistically.

## Performance

Independently measured during this review:

| Metric | Report claim | Measured | Status |
|---|---|---|---|
| Duration | 24.03 s (no tracemalloc) / 30.05 s (with) | 32.10 s (with tracemalloc) | consistent |
| Throughput | 10,404 rows/sec | 7,787 rows/sec (with tracemalloc) | consistent |
| Peak heap | 36.4 MB | **36.8 MB** | ✓ |
| Fingerprint | `e8414edd…` | `e8414edd…` | ✓ |

The throughput difference is `tracemalloc` overhead; the report explicitly distinguishes the two measurements.

**Claimed fixes verified as real:**
- `eligible_gateways` is **65 bytes with exactly 1 distinct value** — the ~125 MB duplication is genuinely gone.
- **0 database calls inside the per-row loop** — no N+1.
- Buffer is `seek(0)`/`truncate(0)` every 20,000 rows — genuine streaming, no hidden accumulation.

Storage: 341 MB total (245 MB heap + 96 MB indexes), 337 bytes/row average. See P2-4 on remaining per-row constant redundancy.

Scaling to millions will not require redesign: memory is flat in dataset size and the write path is a single COPY.

## Test Quality

**Strong overall.** 62 Day 2B tests span database integrity, determinism, provenance, staleness, coherence, distribution bounds, outcome-model unit behaviour, read surface, and cohorts. Notably:

- **4 impossibility tests assert against the database**, not the generator, so an ETL bug cannot pass them; 10 tests use `pytest.raises(IntegrityError)`.
- Distribution bounds use **4σ binomial standard error** rather than fixed percentages — tighter at 250K than the fixed tolerances they replaced, and still valid at fixture scale.
- The `is_synthetic` enforcement test is parametrised across **all six** synthetic tables.

**Regression coverage for the five bugs found during implementation:**

| Lesson | Covered? |
|---|---|
| APPROVED + ELEVATED over-constraint | ✓ `test_approved_may_legitimately_be_slow` |
| Leaked DB connection / hanging test | ✓ context managers throughout + short pool timeout in `conftest` |
| Invalid fixture timestamps | ✓ audited-range guard with explanatory comment |
| Memory blowup (streaming fix) | ✗ **no test** — see P2-2 |
| Duplicated eligibility JSON | ✗ **no compactness assertion** — see P2-3 |

The two gaps are efficiency regressions, not correctness ones: reverting either would still produce a correct, identical fingerprint, so no test fails. Genuine but non-blocking.

## Code Architecture

2,568 lines across nine modules. Dependency graph is **acyclic with correct direction**:

```
rng ← routing, outcome_model
calibration ← outcome_model, generator, verify
routing, outcome_model, rng, calibration ← generator
generator, verify ← cli
```

`calibration.py` is a pure leaf config module; `rng.py` depends on nothing internal. No hidden global state, no gateway IDs hard-coded in the logic modules (`routing.py`, `outcome_model.py`, `rng.py`) — behaviour is data-driven from `calibration.py` and the database.

Responsibilities are cleanly separated. Two minor observations at P2 (generator module size, taxonomy expressed in two live places).

---

## P0 Issues

**None.**

## P1 Issues

### P1-1 — Day 2C incident architecture must adopt Approach B before injection

**Severity:** P1 — architectural direction, must be settled before Day 2C writes code. Not a defect in Day 2B.

**Evidence.** Measured on a real 3-day window (2024-06-01 → 06-04 IST, 2,093 transactions, 109 observed failures, gateway_C at 264 transactions / 7.20%):

| Target gateway_C rate | Failures moved | Control rate | vs baseline |
|---|---|---|---|
| 10% | 7 | 4.54% | 0.92× |
| 20% | 34 | 3.06% | 0.62× |
| **25%** | **47** | **2.35%** | **0.48×** |
| 30% | 60 | 1.64% | 0.33× |

**Why it matters.** Status-conditioned assignment fixes the total number of failures per window. Reallocating failures onto a degrading gateway removes them from healthy ones, so the control group *improves* during the incident — which no real degradation does. This inflates the contrast an RCA engine sees, so a passing evaluation would overstate real capability. It also caps achievable severity: beyond ~35% the window runs out of failures.

**Minimum required fix.** Day 2C adopts **Approach B**: keep `transactions` immutable, and generate incident-period outcomes in a Day 2C-owned synthetic layer with its own provenance prefix (e.g. `simulated_*`) distinct from both `observed_*` and `synthetic_*`. Specified in [DAY2C_INTERFACE_READINESS.md](DAY2C_INTERFACE_READINESS.md) §5. **No Day 2B code change required.**

## P2 Issues

**P2-1 — No failure is ever in the NORMAL latency regime.** All 12,376 failures are `ELEVATED` or `TIMEOUT`, so `latency_regime = NORMAL` implies SUCCESS with certainty. Real systems produce fast declines (an insufficient-funds refusal can return quickly). Consequence: latency-based and failure-rate-based detectors become perfectly redundant, so "latency rose AND failures rose" is tautological rather than corroborating — thinner evidence than Aventum could otherwise present. *Fix: allow a small fraction of failures (e.g. fast issuer declines) into the NORMAL regime.*

**P2-2 — No regression test for the streaming/memory fix.** The 844 MB → 36.4 MB improvement has no guard; reverting to full buffering would keep all 260 tests green. *Fix: assert peak heap stays under a threshold during a fixture-scale generation.*

**P2-3 — No compactness assertion on `eligible_gateways`.** The ~125 MB duplication fix is likewise unguarded; the existing assertion (`selected in eligible`) passes for both compact and verbose forms. *Fix: assert the payload stays under ~128 bytes.*

**P2-4 — Per-row constant redundancy.** `selection_method` (56 chars), `selection_seed` (~45 bytes, fully reconstructible from other columns), and `eligible_gateways` (65 bytes, 1 distinct value) account for roughly 41 MB of the 245 MB heap. Negligible now; ~1.6 GB at 10M rows. *Fix: normalise into the run/policy row, or drop `selection_seed` as derivable.*

**P2-5 — Response taxonomy expressed in two live places.** `calibration.py` (`RESPONSE_TAXONOMY`) and `models.py` (CHECK literal), plus the migration (intentionally frozen, per the Day 2A precedent). A change needs both edits plus a migration. Mitigated by the DB rejecting any mismatch immediately. *Fix: derive the model CHECK from the calibration constant.*

**P2-6 — `generator.py` is 737 lines** and mixes canonical reading, config seeding, generation, fingerprinting, and orchestration. Each function is cohesive and the module is navigable, but it is the natural place for Day 2C additions to accumulate. *Fix: split seeding and fingerprinting into their own modules when Day 2C extends it.*

---

## Final Decision Table

| Area | Status | Severity | Evidence | Action |
|---|---|---|---|---|
| Day 2A regression | PASS | — | 260/260, 198 Day 2A intact, 0 skipped | None |
| DB schema | PASS | — | 7 tables, constraint matrix verified, chain `base→0003` | None |
| Provenance | PASS | — | Adversarial `is_synthetic=false` rejected on all 7 tables; lineage 0 breaks | None |
| Staleness | PASS | — | Controlled mismatch detected; FK blocks writing one | None |
| Reproducibility | PASS | — | Both fingerprints reproduced in this review | None |
| RNG | PASS | — | Identical across 5 processes incl. `PYTHONHASHSEED=random` | None |
| Calibration | PASS | — | Every documented value matches code; Σ(w·p) = observed rate exactly | None |
| Status-conditioned model | PASS | — | Exact in expectation; marginals preserved 12,376 = 12,376 | None |
| Outcome coherence | PASS | — | All 6 impossible combinations = 0; allowed case = 9,395 | None |
| Health model | PASS | — | 100% HEALTHY, multipliers 1.0, single coherent code path | None |
| Gateway baseline | PASS | — | 1.63× spread, no dominance, min 32,202 rows | None |
| Routing policy | PASS | — | Versioned, data-driven eligibility, no false claims | None |
| Latency model | PASS (1 note) | P2 | Regimes separated, right-skewed; no failure in NORMAL | P2-1 |
| Response model | PASS | — | Infra-side 23.3–31.6%; no single-row shortcut | None |
| Flagship cohort | PASS | — | gateway_C 3-day → 9–13σ, recomputed | None |
| Truth model | PASS (1 note) | P2 | 5 layers intact; residual bespoke-query path documented | Day 2C obligation |
| Performance | PASS | — | 36.8 MB measured, 0 N+1, 65-byte JSON, streaming confirmed | P2-4 (scale) |
| Test quality | STRONG | P2 | DB-level impossibility tests, 4σ bounds; 2 efficiency gaps | P2-2, P2-3 |
| Code architecture | PASS | — | Acyclic graph, config isolated, no global state | P2-5, P2-6 |
| Day 2C readiness | **CONDITIONAL** | **P1** | No migration needed; outcome architecture must be decided | **P1-1** |
| **Final verdict** | **APPROVED WITH REQUIRED FIXES** | **P1 × 1** | — | Adopt Approach B |
