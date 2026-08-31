# Aventum — Final Handoff (Day 5)

Everything Day 5 created or rewrote, plus every Markdown document written or updated in
this session. Paths mirror the real repo, so any file here can be dropped back onto its
original location.

**43 files.** All source files verified byte-identical to the live repo at copy time.

---

## Status

# DAY 5 COMPLETE — AVENTUM FULLY FUNCTIONAL AND DEMO READY

`602/602 tests pass` · Alembic head `0007` · canonical and generation fingerprints
unchanged · 250,000 observed transactions untouched.

---

## What Day 5 added

Two capabilities did not exist before today, and one of them the system had explicitly
deferred: Day 4A ends by recording `"recovery_claim": "NONE — Day 5 owns verification"`.

| | Before | After |
|---|---|---|
| HTTP API | **none existed** — backend was CLI-only | 19 endpoints, the browser's only surface |
| Verification | **did not exist** | independent verifier, own thresholds, can return `RECOVERY_NOT_VERIFIED` |
| Batch measurement | did not exist | population-level, counted from persisted rows |
| Frontend data | 304 lines of fixtures | **fixtures deleted** |
| Workflow state | local React booleans | derived server-side on every request |
| Tests | 545 | **602** (+57, none removed or weakened) |

**Flagship, verified in a real browser:** `gateway_C → gateway_A @ 30%` · 9.26σ ·
confidence 68.81% · evidence strength 74.03% · policy PERMITTED · human approved ·
executed through `SimulatedRoutingAdapter` · independently verified **RECOVERY_EFFECTIVE**
· failure rate **20.83% → 17.42%** · 79 transactions moved · **₹19,126.26 projected versus
₹14,668.00 actually recovered** · attainment 100% · 7/7 integrity checks pass.

---

## Folder contents

### `docs/` — every Markdown file written or updated in this session

| File | State | What it is |
|---|---|---|
| `DAY5_IMPLEMENTATION_REPORT.md` | **created** | The Day 5 report: system contract, architecture, verification design, red team, performance, limitations |
| `DAY5_HANDOFF_MANIFEST.md` | **created** | Day 5 phase manifest (copy of `handoff/5/MANIFEST.md`) |
| `DAY4B_P1_FIX_REPORT.md` | **updated** | Day 4B P1 reliability fix, rewritten today with verified numbers after the silent-truncation root cause was found |
| `DAY4B_P1_FIX_MANIFEST.md` | **created** | Day 4B P1 phase manifest (copy of `handoff/4b-p1fix/MANIFEST.md`) |
| `PROJECT_README.md` | **updated** | Repo README (copy of `README.md`), updated with Day 4B/Day 5 status, run instructions and the 602-test description |

Not included, because I did not write them: `AGENTS.md`, `CLAUDE.md` and
`src/imports/pasted_text/*.md` arrived with the Figma export.

### `backend/` — created by Day 5

| Path | Purpose |
|---|---|
| `aventum_verification/constants.py` | Verification's **own** thresholds and vocabulary |
| `aventum_verification/models.py` | The `verifications` table |
| `aventum_verification/verify.py` | The independent verifier |
| `aventum_verification/batch.py` | Population-level recovery measurement |
| `aventum_verification/__init__.py` | Package surface |
| `aventum_api/app.py` | 19 endpoints |
| `aventum_api/deps.py` | Engine, session-per-request, error vocabulary |
| `aventum_api/serializers.py` | Wire shapes and server-assigned provenance labels |
| `aventum_api/config.py` | Env-driven config, closed CORS origin list |
| `aventum_api/demo.py` | Allow-listed demo reset |
| `aventum_api/__init__.py` | Package surface |
| `migrations/versions/0007_day5_verification.py` | One additive table, 4 CHECKs, 1 unique constraint |
| `tests/test_day5_verification.py` | 29 tests, weighted toward **negative** outcomes |
| `tests/test_day5_api.py` | 28 tests, weighted toward **refusals** |
| `.env.example` | Updated with the Day 5 API variables |

### `frontend/` — created or rewritten by Day 5

**Created:**

| Path | Purpose |
|---|---|
| `src/lib/types.ts` | Domain model, using the backend's own field names |
| `src/lib/api.ts` | The single door to the backend — one fetch, one error type |
| `src/lib/hooks.ts` | `useResource` / `useMutation` / `usePolling` |
| `src/lib/format.ts` | Presentation formatting only; no arithmetic that decides anything |
| `src/lib/recovery.ts` | Backend state string → label, tone, step progress |
| `src/components/states.tsx` | Loading, error, empty, stopped states + error boundary |
| `src/components/GatewayHealthChart.tsx` | Real gateway comparison, replacing a fabricated timeline |
| `.env.example` | `VITE_AVENTUM_API_URL` |

**Rewritten to use real backend data** (previously fixture-driven): `src/App.tsx` ·
`components/Shell.tsx` · `components/ui.tsx` · `components/AgentPanel.tsx` ·
`incident/IncidentWorkspace.tsx` · `incident/DecisionState.tsx` ·
`incident/tabs/{CommandCenter,EvidenceRCA,Simulation,Recommendation,Approval,ExecutionVerification}.tsx`
· `screens/{Overview,Audit}.tsx`

**Deleted from the repo** (so not present here): `src/lib/data.ts` (304 lines of
fixtures) and `src/components/HealthTimeline.tsx` (a fabricated seven-day series with no
backend behind it).

Not included: `package.json`, `vite.config.ts`, `index.html`, `index.css` and the Figma
`src/imports/` — these arrived with the export and Day 5 did not change them. `index.css`
in particular is untouched, which is what preserves the visual language.

---

## Why verification is independent

Independence is about **who owns the standards** and **whether the answer can come back
negative**, not about running the physics twice:

1. **Different inputs.** The recommendation was authorised from the simulation summary;
   verification measures the *adapter's* post-action population against the
   *execution-time* baseline. The adapter re-derives its numbers rather than echoing the
   simulation, so the two can genuinely disagree.
2. **Different thresholds, owned here.** `constants.py` imports nothing from
   `aventum_policy` — asserted by a test that parses the module's imports rather than
   grepping its prose, because both modules *discuss* the policy layer in their docstrings.
3. **It can say no.** `RECOVERY_NOT_VERIFIED` is reachable from a successfully executed
   action. Attaining under 20% of the projection produces it **even when the raw movement
   was positive**, because a projection missed that badly means the model that authorised
   the action did not describe reality.
4. **Integrity before merit.** Seven checks re-walk the lineage and recompute the execution
   fingerprint. A number whose provenance fails is never graded on how good it looks.

Three honesty properties in batch measurement: the two money figures are **never summed**;
`RECOVERY_NOT_VERIFIED` contributes **zero** recovered GMV; an empty population returns
**UNAVAILABLE, not 0%**.

---

## Defects found and fixed during Day 5

Several were found by using the product rather than reading it.

1. **`/api/health` took 4.06 s.** Every call probed a *down* Ollama, discovered by timeout
   rather than refusal. Health is polled by every open tab, so it was the slowest thing in
   the product and pushed concurrent callers past their own client timeout — leaving System
   Health stuck on "CHECKING". A 15 s probe cache took it to **3.6 ms**.
2. **The approval-request endpoint re-ran the whole decision pipeline**, purely to obtain a
   policy object for the approval payload. It emitted 13 duplicate `SIMULATION_COMPLETED`
   events and, more seriously, risked minting a recommendation different from the one the
   operator was looking at. Audit went from 32 events to 19.
3. **My own population-stability check compared two different populations** — the affected
   cohort (264) against the full-window allocation (2093). It failed on every healthy run
   and turned a correct `RECOVERY_EFFECTIVE` into `RECOVERY_NOT_VERIFIED`.
4. **Gateway health read HEALTHY during a CRITICAL incident.** The synthetic health windows
   are year-long baselines; the incident's effect arrives through multipliers. Now uses the
   engine's own `runtime_profile_for`, so gateway_C shows 21.7% effective failure against a
   6.2% baseline.
5. **A broad `except` silently hid a real shape bug** — `world.health` maps gateway → *list*
   of windows, not a single object. It rendered an empty panel with no server-side trace.
6. **Truth tags truncated to "DETERMINIS…"** in narrow metric columns.
7. **Failed health checks rendered as "CHECKING"**, understating an outage as a slow load.
8. **Projected GMV showed ₹0.00 before anything was proposed**, implying "we projected
   zero" rather than "nothing has been proposed yet".
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

## Data integrity

| | Value | Status |
|---|---|---|
| Canonical fingerprint | `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8` | **unchanged** |
| Generation fingerprint | `e8414edd5a58c6cf04876e1bf48ca9a5564cf8d77da8eca4201c1732f52fe3c8` | **unchanged** |
| Observed transactions | 250,000 | **unchanged** |
| Alembic head | `0007` | +1 additive table |

Migration 0007 contains no ALTER and no DROP. No expected fingerprint was edited.

---

## Running it

```bash
cd backend && docker compose up -d
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m uvicorn aventum_api.app:app --port 8000

cd "../razorpay frontend new" && npm install && npm run dev
```

The agent is optional — `ollama serve && ollama pull qwen3:8b`. Everything else works
without it, and the console says so rather than fabricating an explanation.

`POST /api/demo/reset` restores a clean flagship state with no manual SQL.

---

## Known limitations

- **No authentication.** The approver identity is typed, not authenticated. A production
  deployment would need real identity before the approval trail could be relied on.
- **The agent path has had less live exercise than the deterministic one.** Its
  *unavailable* degradation is thoroughly verified — the entire flagship completes with
  Ollama down — but the agent-driven UI path is wired and typed more than it is exercised.
- **No time-series telemetry**, so latency medians render `UNAVAILABLE`.
- **Capacity is `UNAVAILABLE` throughout** — none exists anywhere in the system.
- **Verification is pre/post on one cohort, not a randomised control.** No concurrent
  untreated arm exists in this data; the limitation is persisted with every verification.
- **One measurement window.** No durability claim is made.
- **The overview ranks by significance**, so a systemic 24.52σ incident outranks the
  flagship gateway_C incident at 9.26σ.

Everything here is a synthetic incident with simulated execution. No production
infrastructure is contacted and no recovery of real money is claimed.
