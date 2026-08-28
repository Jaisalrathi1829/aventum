# Day 4A Handoff — The Deterministic Decision Core

Snapshot of every file created or changed by **Day 4A**: counterfactual simulation, business impact, deterministic risk, the policy gate, recommendation, human approval, simulated execution, and audit. Paths mirror the real repo.

All 26 files verified byte-identical to the live repo at copy time.

---

## Status

# DAY 4A COMPLETE — DETERMINISTIC CORE READY FOR DAY 4B

**472/472 tests pass** (378 pre-existing + 94 new). Observed data, canonical fingerprint, and Day 2B generation fingerprint all byte-identical. **12/12 red-team scenarios blocked**, verified against the real 250,000-row dataset. No Qwen, no Ollama, no agent loop — asserted by test, not merely omitted.

---

## What Day 4A does, in one table

| | Value |
|---|---|
| Migration | `0006` — 7 tables, additive only, chain reproducible from clean state |
| Candidates simulated per incident | **13** (NO_ACTION + 10/20/30% to 4 eligible gateways) |
| Policy gates | **13**, all must pass, fail-closed |
| Best candidate (flagship) | `REROUTE:gateway_C→gateway_A@30.0` |
| Expected GMV retained | **19,126.26 INR** (projected — never "recovered") |
| Incident → recommendation | **152.7 ms** (target < 300 s) |
| Recommendations carrying a rationale | **0** — the spine decides without a model |
| Modules naming ground truth | **0 of 20** |
| `capacity_utilization` populated | **0 rows** — UNAVAILABLE, never estimated |

---

## The three structural guarantees

1. **Fabricated numbers cannot enter a recommendation.** `build_recommendation()` has no numeric parameter at all — every figure is read server-side from the persisted `simulation_id`. Not a validation that could be skipped; an absent parameter that cannot be passed.

2. **Duplicate execution is impossible.** `SHA256(recommendation_id ‖ approval_id ‖ adapter_name)` is UNIQUE on `actions`, and the row is inserted *before* the adapter runs. Proven with two real threads on two real connections: 1 adapter invocation, 1 action row.

3. **Stale actions cannot execute.** The input fingerprint is re-derived from the current world and the full 13-gate policy is re-run at execution time. Both are derived checks — neither can be satisfied by editing a status column.

---

## Two real defects found during implementation

**Reroute selection was correlated with the incident's own failure draw.** The selection hash payload was byte-identical to Day 3's outcome-draw key, so ordering by it selected exactly the transactions the incident had damaged. A 10% reroute "rescued" 26 of 26 rows; benefit was overstated ~5×. Fixed with a domain separator; pinned by a regression test. See `docs/DAY4A_IMPLEMENTATION_REPORT.md` §26.

**`ck_action_executed_coherent` rejected rollback.** A ROLLED_BACK action legitimately keeps its `executed_at` — it really was executed, then reverted. The constraint caught its own error.

---

## Files

```text
backend/
├── aventum_counterfactual/        # simulator, impact, risk, optimization
│   ├── __init__.py                # model/config versions
│   ├── constants.py               # candidate set, bounds, invalid reasons, honesty markers
│   ├── models.py                  # counterfactual_simulations + agent tables (unused in 4A)
│   ├── source.py                  # THE ONLY Day 4 module permitted to name a synthetic table
│   ├── fingerprint.py             # input/simulation/recommendation/idempotency digests
│   ├── simulator.py               # the controlled counterfactual; reuses Day 2B/3 machinery
│   ├── impact.py                  # deterministic business impact; the GMV objective
│   ├── risk.py                    # six named components; capacity stays UNAVAILABLE
│   └── optimize.py                # NO_ACTION-first sweep, ordered selection, 95% tie-break
├── aventum_policy/                # the safety authority
│   ├── __init__.py                # POLICY_VERSION
│   ├── constants.py               # system-owned thresholds; unreachable from any caller
│   └── gate.py                    # 13 explicit gates, fail-closed
├── aventum_action/                # recommendation → approval → execution → audit
│   ├── __init__.py
│   ├── models.py                  # recommendations, approvals, actions, audit_events
│   ├── audit.py                   # append-only; emit() is the only write verb
│   ├── recommendation.py          # the builder with no numeric parameter
│   ├── approval.py                # human-only, fingerprint-bound, expiring
│   ├── adapter.py                 # RoutingActionAdapter Protocol + SimulatedRoutingAdapter
│   ├── execute.py                 # 14-check revalidation, idempotent execution, rollback
│   ├── handoff.py                 # the Day 5 interface + provenance chain
│   ├── pipeline.py                # the spine, end to end
│   └── cli.py                     # the human approval interface
├── migrations/versions/
│   └── 0006_day4_action_layer.py  # 7 tables, additive only
└── tests/
    ├── test_decision_core.py      # 94 new tests
    └── conftest.py                # +7 Day 4 tables in the TRUNCATE list (only change)

docs/
└── DAY4A_IMPLEMENTATION_REPORT.md
```

---

## Reproducing it

```bash
cd backend && .venv/Scripts/python -m alembic upgrade head
cd backend && .venv/Scripts/python -m aventum_action.cli decide 1
cd backend && .venv/Scripts/python -m aventum_action.cli request 1
cd backend && .venv/Scripts/python -m aventum_action.cli approve 1 --approver <name>
cd backend && .venv/Scripts/python -m aventum_action.cli execute 1 1
cd backend && .venv/Scripts/python -m aventum_action.cli verify 1
```

`request`, `approve`, and `execute` are separate commands on purpose. There is no `--auto-approve` flag, because such a flag is exactly the affordance that erodes a human gate.

---

## What is NOT here

No Qwen, no Ollama client, no tool registry, no agent loop, no frontend, no Day 5 verification engine, no real payment or routing API. Day 4A stops at a simulated execution with a complete audit trail and makes **no recovery claim** — Day 5 owns verification.
