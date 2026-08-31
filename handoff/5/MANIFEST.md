# Day 5 Handoff — Productization, Frontend Integration, Verification

Snapshot of everything Day 5 created or changed. Paths mirror the real repo.

39 files: the verification engine, the HTTP API, migration 0007, the rewired frontend,
the Day 5 test suites, and the implementation report.

---

## Status

# DAY 5 COMPLETE — AVENTUM FULLY FUNCTIONAL AND DEMO READY

Alembic head `0007`, canonical and generation fingerprints unchanged, 250,000 observed
transactions untouched, 602 tests passing.

---

## What Day 5 added

| | Before | After |
|---|---|---|
| HTTP API | **none existed** | 19 endpoints |
| Verification | **did not exist** | independent deterministic verifier |
| Batch measurement | did not exist | population-level, counted from rows |
| Frontend data | 304 lines of fixtures | **fixtures deleted** |
| Workflow state | local React booleans | derived server-side per request |
| Tests | 545 | **602** (+57, none removed) |

**Flagship:** `gateway_C → gateway_A @ 30%` · projected ₹19,126.26 · executed through
`SimulatedRoutingAdapter` · verified **RECOVERY_EFFECTIVE** · failure rate 20.83% → 17.42%
· 79 transactions moved · **₹14,668.00 actually recovered** · attainment 100% · 7/7
integrity checks pass.

---

## The two capabilities that did not exist

### Independent verification

Day 4A ends by refusing to claim recovery — `execute.py` records
`"recovery_claim": "NONE — Day 5 owns verification"`. This answers it.

Independence is about **who owns the standards** and **whether the answer can come back
negative**, not about running the physics twice:

1. **Different inputs** — the recommendation was authorised from the simulation summary;
   verification measures the adapter's population against the execution-time baseline.
2. **Different thresholds, owned here** — `constants.py` imports nothing from
   `aventum_policy`, asserted by a test that parses imports rather than grepping prose.
3. **It can say no** — `RECOVERY_NOT_VERIFIED` is reachable from a successful execution.
   Attaining under 20% of the projection produces it *even when movement was positive*.
4. **Integrity before merit** — seven checks re-walk lineage and recompute the execution
   fingerprint. A number whose provenance fails is never graded on how good it looks.

### Batch recovery measurement

Counts and metrics over the whole persisted population. Three honesty properties:

- The two money figures are **never summed** — one is a projection over recommendations,
  the other a measurement over verified actions.
- `RECOVERY_NOT_VERIFIED` contributes **zero** recovered GMV.
- An empty population returns **UNAVAILABLE, not 0%** — "we have not tried yet" and "we
  tried and failed" are different claims.

---

## Defects found and fixed during Day 5

1. **`/api/health` took 4.06 s.** Every call probed a *down* Ollama, discovered by timeout
   rather than refusal. Health is polled by every open tab, so it was the slowest thing in
   the product and pushed concurrent callers past their client timeout, leaving System
   Health stuck on "CHECKING". A 15 s probe cache took it to **3.6 ms**.

2. **The approval-request endpoint re-ran the whole decision pipeline.** It did so only to
   obtain a policy object for the approval payload — emitting 13 duplicate
   `SIMULATION_COMPLETED` events and, more seriously, risking minting a recommendation
   different from the one the operator was looking at. Audit went from 32 events to 19.

3. **The population-stability integrity check compared two different populations.** It
   measured the affected cohort (264) against the full-window allocation (2093), so it
   failed on every healthy run and turned a correct `RECOVERY_EFFECTIVE` into
   `RECOVERY_NOT_VERIFIED`. Now compares allocation totals, which is the real invariant.

4. **Gateway health read HEALTHY during a CRITICAL incident.** The synthetic health windows
   are year-long baselines; the incident's effect arrives through multipliers. Now uses the
   engine's own `runtime_profile_for`, so gateway_C correctly shows 21.7% effective failure
   against a 6.2% baseline.

5. **A silent `except` hid a real shape bug.** `world.health` maps gateway → *list* of
   windows, not a single object; the broad catch rendered an empty panel with no trace. Now
   logs server-side.

6. **Truth tags truncated to "DETERMINIS…"** in narrow metric columns. A half-rendered
   provenance label is worse than a wrapped one.

7. **Failed health checks rendered as "CHECKING".** An outage was being understated as a
   slow load. Now reports UNREACHABLE / UNKNOWN.

8. **Projected GMV showed ₹0.00 before anything was proposed**, implying "we projected
   zero". Now UNAVAILABLE until the population is non-empty.
9. **Mutations committed after the response was sent.** `get_session` commits in a
   FastAPI yield-dependency's cleanup, which runs *after* the response goes out. A client
   that re-read the instant a mutation returned observed pre-commit state. It surfaced as
   the agent panel: a run would succeed, be written, and the panel would still say "no
   agent run exists for this incident" — the row was real, the immediate re-read just
   could not see it yet. All eight mutating routes now commit before returning, so
   read-after-write holds for any client fast enough to try.
10. **The agent panel contradicted itself while working.** During the ~60 s a local 8B
    model takes, it showed a "NOT RUN" pill, the text "No agent run exists for this
    incident", and a disabled "Agent analysing…" button *simultaneously* — three
    disagreeing signals and no sign of life. It now shows a RUNNING pill, a live elapsed
    counter, and the honest expectation ("45–90 seconds on a local 8B model; the
    deterministic analysis on this page is already complete and is not waiting on this").

---

## Frontend: what changed, and what deliberately did not

**The visual language is preserved.** `index.css` design tokens are untouched — the same
grounds, accent, typography, and crucially the same **truth-category hues**. The Figma
source had already encoded `--observed`, `--synthetic`, `--simulated`, `--projected`,
`--verified`, `--agent`, `--deterministic`, `--human`, which is exactly the §9 truth model.
That palette drove the integration rather than being reworked around it.

Four visual changes, each for correctness:

| Change | Reason |
|---|---|
| Seven-day timeline → gateway failure-probability chart | The timeline was a fabricated series with no backend and no endpoint that could make it real (§5) |
| Metric headers wrap instead of truncate | "DETERMINIS…" is worse than a second line |
| "Toggle agent unavailable" demo button removed | Availability is now a real probe; a fake outage button has no place beside a real one |
| System Health shows OK / DOWN / CHECKING | The prototype hard-coded four green rows |

**`src/lib/data.ts` (304 lines of fixtures) and `HealthTimeline.tsx` are deleted.** No
authoritative path depends on a fixture.

---

## State ownership

`RecoveryState` is derived from persisted rows on **every request**, never stored as a
status column, and computed **backwards from the furthest-progressed artefact** — reading
forwards would report "awaiting approval" for an incident already verified.

The prototype's `FlowState {approved, executed, verified}` is gone. Those were
authoritative business booleans that a browser refresh silently rewound. Verified live: a
full page reload mid-workflow reconstructs `APPROVED` from the backend.

---

## Red team

| # | Attack | Result |
|---|---|---|
| 1 | Backend unavailable | `NETWORK_UNREACHABLE` + retry; health shows UNREACHABLE |
| 2 | Agent unavailable | Full flagship completed; panel states outage, fabricates nothing |
| 3 | Malformed API response | `MALFORMED_RESPONSE`, no retry offered |
| 4 | Network timeout | Aborted at budget → `TIMEOUT` |
| 5 | Stale recommendation | `is_stale` re-derived; execution not offered |
| 6 | Expired approval | 409 |
| 7 | Rejected approval | Execution refused 409 |
| 8 | Tampered approval fingerprint | `REJECTED / APPROVAL_FINGERPRINT_MISMATCH` |
| 9 | Failed verification | `RECOVERY_NOT_VERIFIED`, zero recovered GMV |
| 10 | NO_ACTION | Reported as a stop, not a failure |
| 11 | Policy block | Never reaches approval; 409 |
| 12 | Duplicate approval | 409 (partial unique index) |
| 13 | Duplicate execution | Idempotent — same action id |
| 14 | Duplicate verification | Idempotent — same verification id |
| 15 | Refresh mid-workflow | State reconstructed from backend |
| 16 | Forged frontend state | Ignored; only `decision`/`identity`/`note` are read |
| 17 | Injected numerics (`gmv: 999999999`) | Ignored; ₹19,126.26 unchanged |
| 18 | Provenance mismatch | Integrity check fails → not verified |
| 19 | Approval without identity | 400 `APPROVER_REQUIRED` |
| 20 | Forged decision values incl. SQL-shaped | 400 `INVALID_DECISION` |
| 21 | SQL / credential exposure | None across all read endpoints |
| 22 | Internal error detail | Stable code + one sentence; verified live |
| 23 | Demo reset integrity | Observed data and Day 3 analysis untouched |

---

## Files

| Path | Purpose |
|---|---|
| `backend/aventum_verification/constants.py` | Verification's OWN thresholds and vocabulary |
| `backend/aventum_verification/models.py` | `verifications` table |
| `backend/aventum_verification/verify.py` | The independent verifier |
| `backend/aventum_verification/batch.py` | Population-level measurement |
| `backend/aventum_api/app.py` | 19 endpoints |
| `backend/aventum_api/deps.py` | Engine, session-per-request, error vocabulary |
| `backend/aventum_api/serializers.py` | Wire shapes + server-assigned provenance labels |
| `backend/aventum_api/config.py` | Env-driven config, closed CORS list |
| `backend/aventum_api/demo.py` | Allow-listed demo reset |
| `backend/migrations/versions/0007_…py` | One additive table, 4 CHECKs, 1 unique |
| `backend/tests/test_day5_verification.py` | 29 tests, weighted toward negative outcomes |
| `backend/tests/test_day5_api.py` | 28 tests, weighted toward refusals |
| `frontend/src/lib/{types,api,hooks,format,recovery}.ts` | Domain model, single API door, data hooks |
| `frontend/src/components/{states,GatewayHealthChart}.tsx` | Loading/error/stop states, real chart |
| `frontend/src/{App,screens,incident}/…` | Rewired to real data |

---

## Data integrity

| | Value | Status |
|---|---|---|
| Canonical fingerprint | `12dec963…f4b8` | **unchanged** |
| Generation fingerprint | `e8414edd…2fe3c8` | **unchanged** |
| Observed transactions | 250,000 | **unchanged** |
| Alembic head | `0007` | +1 additive table |

No expected fingerprint was edited. Migration 0007 contains no ALTER and no DROP.

---

## Not touched

No authentication was added (approver identity is typed, not authenticated — a documented
prototype boundary). No real payment execution. No model swap. No Day 4A redesign. Day 3
and Day 4 business logic is unchanged; the only backend edits outside the new packages were
none at all.
