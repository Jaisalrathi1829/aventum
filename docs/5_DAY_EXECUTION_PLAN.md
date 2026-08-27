_Aventum internal plan — five-day compressed schedule._

# Five-Day Execution Plan

Project Aventum compressed to five days. Each day's scope is fixed; deliverables are the minimum that keeps the flagship demo credible, not a maximal feature list.

---

## DAY 1 — Data and Architecture

**Status: COMPLETE**

Dataset audit, feasibility assessment, canonical data architecture, routing-dataset calibration audit. See `docs/DAY1_REPORT.md`, `docs/AVENTUM_CANONICAL_SCHEMA.md`, `docs/DATABASE_DESIGN.md`, `docs/ROUTING_DATASET_DECISION.md`.

---

## DAY 2 — Canonical Pipeline + Synthetic Infrastructure

**Status: COMPLETE**

- **Day 2A:** canonical transaction ingestion (`upi_transactions_2024` → `transactions`, 250,000 rows), independently reviewed, P1 provenance defect found and fixed.
- **Day 2B:** synthetic payment-infrastructure baseline (gateways, routing, latency, response codes, health), independently reviewed — 0 P0, 1 P1 (architectural decision for Day 3, locked as Approach B), 6 P2 (deferred).
- **Day 2 closeout:** release gate passed, interfaces frozen, Day 3 contract locked. See `docs/DAY2_FINAL_HANDOFF.md`.

---

## DAY 3 — Incident Injection + Simulated Outcomes + Anomaly Detection + Evidence-Backed RCA

**Objective:** `inject incident → generate simulated outcomes → detect anomaly → collect evidence → produce explainable RCA`, for exactly the golden scenario.

**Mandatory deliverables:**

1. Golden incident injected: gateway_C, 3-day window, 20–25% degraded rate, via a `DEGRADED` window in `synthetic_gateway_health_states` (no migration).
2. Simulated incident-outcome layer (Approach B) — `transactions` untouched, new simulated-provenance rows for the incident-period cohort.
3. Deterministic anomaly detection recovering the injected signal (~9–13σ).
4. Deterministic, traceable evidence collection (`incident_evidence`).
5. RCA result citing evidence, correctly naming gateway_C, with confidence — never reading `ground_truth_root_cause`.
6. Full test suite for the new layer: injection determinism, signal coherence, control-group non-contamination, detection correctness, evidence traceability, ground-truth isolation.

**Explicit out of scope:**

- Counterfactual ("what if rerouted") simulation.
- Any recommendation, confidence-bounded action, or approval step.
- Qwen/Ollama or any LLM/agent integration.
- Frontend.
- A generalized multi-incident framework — one golden scenario, done correctly.
- Rollback/execution/post-action verification.

**Acceptance gate:** see `docs/DAY3_IMPLEMENTATION_CONTRACT.md` §Acceptance Gate (8 conditions). All 260 Day 2 tests remain green; new Day 3 tests pass; `incidents.ground_truth_root_cause` is verified unreferenced by the detection/RCA code path.

---

## DAY 4 — Counterfactual Simulation + Qwen Agent + Bounded Recommendation + Human Approval

**Objective:** given Day 3's RCA result, simulate alternative routing outcomes, let a local Qwen-backed agent orchestrate evidence retrieval and explanation, and produce a bounded, human-approvable recovery recommendation.

**Mandatory deliverables:**

1. Counterfactual simulator consuming Day 3's evidence + Day 2B's calibrated gateway profiles to project "reroute away from gateway_C" outcomes — clearly labeled as modeled projections, never observed fact (same truth-model discipline as Day 2B).
2. Qwen (Ollama-hosted) agent wired to tool-based evidence retrieval over Day 3's outputs — the LLM reasons over evidence, it does not invent numbers (per the project's LLM-not-authoritative principle).
3. A recommendation object: target segment, target gateway, bounded traffic percentage, duration, expected benefit, confidence, risk — values from the deterministic simulator, never from the LLM directly.
4. A human-approval gate: recommendation is presented, held pending, and only proceeds on explicit approval.
5. Deterministic safety/policy engine enforcing bounds on any action the recommendation could trigger (the LLM cannot bypass this).

**Explicit out of scope:**

- Actual execution against any real or simulated live system — Day 4 stops at an approved-but-not-yet-executed recommendation, unless Day 5's flagship flow requires execution to close the loop (decide at Day 5 planning, not now).
- Frontend.
- Multi-incident or multi-scenario generalization.

**Required inputs from Day 3 (fixed by `docs/DAY3_IMPLEMENTATION_CONTRACT.md`):** the Incident, Simulated outcome, Detection, RCA evidence, and RCA result interfaces exactly as specified there. Day 4 does not renegotiate Day 3's output shape.

---

## DAY 5 — Frontend + End-to-End Integration + Verification + Audit Trail + Hardening + Flagship Demo

**Objective:** wire the full pipeline into a demonstrable end-to-end flow for the golden incident scenario, with a real frontend, verified post-action outcome, complete audit trail, and enough hardening to survive a live demo.

**Mandatory deliverables:**

1. Frontend surfacing: Detect → Diagnose → Explain → Simulate → Recommend → Approve → (Execute) → Verify → Audit for the golden scenario, using Day 2B's `observed_*`/`synthetic_*` provenance discipline visibly in the UI — synthetic and observed data must never look the same on screen.
2. End-to-end integration test running the full chain against the frozen 250,000-row canonical dataset and the golden incident.
3. Verification step: pre-action baseline vs. post-action outcome (post-action outcome is itself a simulated continuation, per Day 2's static-dataset limitation — label it as such).
4. Complete audit trail: every stage's inputs/outputs logged and queryable, tying back to the same provenance chain established in Day 2 (source SHA-256 → ingestion run → generation run → incident → recommendation → approval → outcome).
5. Hardening pass: fix anything from Day 2's deferred P2 list that turns out to matter for demo stability (see `docs/DAY2_FINAL_HANDOFF.md` §Deferred Technical Debt) — otherwise leave it deferred.
6. Flagship demo script/run for the golden scenario, rehearsed end-to-end.

**Explicit out of scope:**

- New incident scenarios beyond the golden one (unless trivial and time permits after the golden scenario is solid).
- Production deployment concerns beyond what the demo needs.
- Any expansion of the canonical dataset or synthetic gateway universe.

**Acceptance gate:** the golden incident scenario runs end-to-end through the real frontend, produces a correct RCA, a bounded recommendation, a human approval step, a verified outcome, and a complete, inspectable audit trail — with observed, synthetic, simulated, ground-truth, and agent-conclusion data visibly distinguished throughout, exactly as the truth model in `docs/DAY2B_TRUTH_MODEL.md` requires.

---

## Cross-cutting rules for Days 3–5

These are not per-day items — they apply throughout and are restated here so no day accidentally violates them:

- `transactions` (Day 2A) is immutable, forever.
- `baseline-v1` gateway profiles and routing policy (Day 2B) are never mutated — new versions are added instead.
- Ground truth (Day 3+) never enters any diagnosis, detection, or RCA code path — evaluation only.
- The Nigerian calibration dataset is never joined to UPI data, at any layer, in any day.
- New randomness anywhere in the pipeline uses the Day 2B deterministic RNG architecture (`aventum_synth.rng`), never Python's salted `hash()` or an unseeded `random`.
- Every new layer (simulated outcomes, agent conclusions, recommendations) gets its own explicit provenance marker, following the `observed_*` / `synthetic_*` naming precedent — never a flat, unlabeled record.
