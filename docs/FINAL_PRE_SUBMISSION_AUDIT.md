# FINAL PRE-SUBMISSION AUDIT

_Audit-only pass. No production code, frontend code, test, schema, migration, fingerprint,
business logic, model configuration or API contract was modified. This document is the
sole artifact created._

**Audit date:** 2026-08-31 · **HEAD:** `8dd85b4` · **Alembic head:** `0007`

---

## Executive Result

### HIGH-RISK — FIX BEFORE SUBMISSION

The product is **genuinely functional end to end**. The flagship workflow is reproducible,
the deterministic spine is sound, verification is genuinely independent and can fail, the
authority chain holds under attack, and 602/602 tests pass against live infrastructure.
I could not find a way to bypass policy, approve without a human, execute without an
approval, forge a numeric value, or reach production execution.

It is **not** ready to submit, for four reasons that are each confirmed and reproducible:

1. **Everything after Day 4A is uncommitted.** `git HEAD` is the Day 4A commit. The agent
   layer, the entire API, verification, batch measurement and the whole frontend exist
   only in the working tree. A judge cloning the repository receives a product with no
   API, no frontend and no verification. **This alone makes the submission fail.**
2. **A double-click on Approve writes two `APPROVAL_DECIDED` audit events** for one human
   decision. Reproduced through the real UI, not just synthetically. The audit trail is
   this product's central claim, and it can be made to misreport how many times a human
   decided.
3. **An agent-authored recommendation records `agent_run_id = NULL`**, so the UI labels it
   "AGENT RUN: NONE (deterministic)" while the same API payload carries
   `rationale_truth: AI_GENERATED`. One object asserts both "no agent was involved" and
   "this text is AI-generated." In a product whose thesis is provenance integrity, that
   is a contradiction a hostile judge would find.
4. **With the database down, every endpoint hangs for over 120 seconds**, including
   `/api/health`, whose entire job is to report that the database is down. The operator
   sees a 30-second skeleton and is then told the **API** is unreachable when the API is
   healthy.

None of these is a P0 — nothing corrupts observed data, produces an unsafe action, or
makes a false financial claim. All four are P1: they will be seen, and three of them
undermine claims the product makes about itself.

---

## Audit Method

Evidence was gathered by execution, not inspection, wherever execution was possible.

| Method | What was done |
|---|---|
| Live database | Direct SQL against the running PostgreSQL for every claim about persisted state |
| Live API | 19 endpoints enumerated from the running app's OpenAPI; ~90 adversarial requests |
| Real browser | Full flagship walkthrough at 1440×900 and 1280×800, reload at every workflow stage, second tab, double-click attacks |
| Real agent | 6 live `qwen3:8b` runs across 5 scenario types |
| Automated tests | Full suite executed (`602 passed in 957.50s`) |
| Concurrency | Threaded barrier-synchronised requests, 5-way, against approval / execute / verify |
| Fault injection | Database stopped, Ollama stopped, backend stopped, malformed JSON, hostile HTTP server |
| Static analysis | AST parse for GET side effects, import-graph check for verification independence, contrast/heading/label measurement in the live DOM |

**Deliberate limits.** Row-level state was manipulated on the *disposable demo database*
to construct outcomes the system cannot reach on its own (a regression, a partial
recovery, a tampered fingerprint), and `POST /api/demo/reset` restored it afterwards.
Protected data was verified unchanged before and after. No schema, migration, fingerprint
or test was altered.

---

## 1. Baseline Verification

Every claimed baseline was checked against the live system.

| Claim | Verified value | Result |
|---|---|---|
| 250,000 observed transactions | 250000 | **OK** |
| Canonical fingerprint `12dec963…f4b8` | matches | **OK** |
| Generation fingerprint `e8414edd…2fe3c8` | matches | **OK** |
| Alembic head 0007 | `0007` | **OK** |
| Full suite 602 passing | `602 passed in 957.50s`, exit 0 | **OK** |
| React frontend | Vite 8 / React 19 / TS 5.7, typecheck clean, build clean (291.95 kB, 82.42 kB gzip) | **OK** |
| FastAPI API | 19 operations live | **OK** |
| Qwen3 8B + Ollama | `qwen3:8b` resident, ctx 9216, 4.74 GB VRAM | **OK** |
| Day 3 / 4A / 4B present | 33 public tables, all expected present | **OK** |
| Independent verification | `verifications` table, 3 outcomes reachable | **OK** |
| Batch measurement | counted from persisted rows | **OK** |
| Human approval / simulated execution / append-only audit | present and enforced | **OK** |
| Backend-owned business state | recovery state re-derived per request | **OK** |

**Discrepancies found:** one, and it is the most serious finding in this audit —
`git HEAD` is `8dd85b4` *"Day 4A: deterministic decision core"* dated 2026-08-28, with
**20 uncommitted entries** covering all of Day 4B and Day 5. See AV-01.

Environment: CORS restricted to a closed 4-origin list (5173/4173, localhost + 127.0.0.1);
`.env.example` present for both backend and frontend; no secrets in either.

---

## 2. Critical Findings

**None.** No P0 was found. Specifically ruled out by execution:

- Observed data cannot be mutated by any API path, verification, or demo reset (verified
  across 3 consecutive resets — all 10 protected quantities unchanged).
- No production execution path exists; `SimulatedRoutingAdapter` is the only adapter and
  `is_simulated` carries a database `CHECK (= true)`.
- No unsafe action is reachable: policy, approval, freshness and idempotency are all
  enforced server-side and all refused forged input.
- No false financial claim: projected and measured money are stored, served and rendered
  as separate quantities, and unverified recovery contributes exactly zero.

---

## 3. P1 Findings

---

**ID:** AV-01
**AREA:** Deployment / Repository
**SEVERITY:** P1 · **DEMO LIKELIHOOD:** HIGH · **REGRESSION RISK:** LOW · **USER IMPACT:** HIGH · **FIX EFFORT:** LOW

**TITLE:** All Day 4B and Day 5 work is uncommitted; the repository HEAD is Day 4A

**OBSERVED:**
```
commit : 8dd85b449b88198a688d37c8a9f2771bd509ebec
date   : 2026-08-28
subject: Day 4A: deterministic decision core
dirty  : 20 entries
?? backend/aventum_agent/   ?? backend/aventum_api/   ?? backend/aventum_verification/
?? backend/migrations/versions/0007_day5_verification.py
?? backend/tests/test_agent_layer.py ?? test_day5_api.py ?? test_day5_verification.py
?? razorpay frontend new/   ?? docs/DAY5_IMPLEMENTATION_REPORT.md   ?? handoff/5/ ...
```

**EXPECTED:** The submitted repository contains the product being demonstrated.

**IMPACT:** A judge cloning the repository gets Day 4A only — no HTTP API, no frontend, no
agent layer, no verification, no migration 0007. The demo cannot be reproduced from the
submitted artifact. Every other finding in this audit is moot if this is not fixed.

**EXACT REPRODUCTION:** `git log -1` and `git status --porcelain` in `aventum/`.

**EVIDENCE:** Above. 285 files tracked, 79.79 MiB packed; the untracked set includes all
11 `aventum_api` + `aventum_verification` modules and all 23 frontend source files.

**OWNING LAYER:** INFRA
**ROOT CAUSE:** Proven — no commit was made after Day 4A.
**RECOMMENDED FIX:** Stage and commit Day 4B + Day 5, then push. Verify with a clean
`git clone` into a temporary directory that the API, frontend and migration 0007 are present.
**REGRESSION TEST:** CI (or a documented pre-submission step) that clones the pushed repo
and runs `alembic upgrade head` + `pytest` + `npm run build`.
**STATUS:** OPEN

---

**ID:** AV-02
**AREA:** Approval / Audit integrity / Concurrency
**SEVERITY:** P1 · **DEMO LIKELIHOOD:** MEDIUM · **REGRESSION RISK:** LOW · **USER IMPACT:** HIGH · **FIX EFFORT:** LOW

**TITLE:** Double-clicking Approve writes duplicate `APPROVAL_DECIDED` audit events

**OBSERVED:** In the real browser, two clicks on **Approve** produced **2**
`APPROVAL_DECIDED` events for one approval:
```
(17, 'HUMAN:ops.lead@aventum.demo', 2026-08-31 11:40:23.092761+00)
(18, 'HUMAN:ops.lead@aventum.demo', 2026-08-31 11:40:23.093648+00)
approvals: [(1, 'APPROVED', 'ops.lead@aventum.demo')]
```
Under a 5-way threaded barrier the same endpoint returned **4× HTTP 200** and wrote
**4 events**. Event ids were also emitted out of timestamp order (id 20 at `…56759`
precedes id 19 at `…56901`), so `event_id` ordering does not match `occurred_at` under
concurrency.

**EXPECTED:** Exactly one `APPROVAL_DECIDED` event per approval decision, as with
`ACTION_EXECUTED` and `VERIFICATION_COMPLETED`, both of which behaved correctly under the
same attack.

**IMPACT:** The audit trail — the product's central claim — misreports how many times a
human decided. A reviewer reconstructing "who approved this, and when" sees two identical
human decisions microseconds apart. Final approval state stays correct, so this is
misreporting rather than corruption, but it is misreporting of the human-authority record.

**EXACT REPRODUCTION:** Reach an approval, click **Approve** twice rapidly (or issue two
simultaneous `POST /api/approvals/{id}/decision`), then
`SELECT count(*) FROM audit_events WHERE event_type='APPROVAL_DECIDED'`.

**EVIDENCE:** Above. Contrast with execute (`uq_action_idempotency` → 1 action, 1 event
from 5 concurrent requests) and verify (`uq_verification_identity` → 1 row, 1 event).

**OWNING LAYER:** BACKEND (`aventum_action/approval.py::decide_approval`), with a
contributing FRONTEND gap.
**ROOT CAUSE:** Proven at the layer. Actions and verifications are protected by database
unique constraints; the approval decision has **no equivalent guard** — no row lock, no
unique constraint on the decided transition. `decide_approval` reads status then writes,
and concurrent callers both pass the read. The frontend's `disabled={decide.pending}` does
not help because both clicks fire in the same event-loop tick before React re-renders.
**RECOMMENDED FIX:** Serialise the decision server-side (`SELECT … FOR UPDATE` on the
approval row, or a partial unique index over the decided state). Do **not** rely on
disabling the button — the server must be the guard. Frontend double-submit suppression is
a worthwhile second layer, not the fix.
**REGRESSION TEST:** A threaded 5-way concurrent-decision test asserting exactly one 200
and exactly one `APPROVAL_DECIDED` row, mirroring the existing execute/verify tests.
**STATUS:** OPEN

---

**ID:** AV-03
**AREA:** Provenance / Agent
**SEVERITY:** P1 · **DEMO LIKELIHOOD:** MEDIUM · **REGRESSION RISK:** LOW · **USER IMPACT:** HIGH · **FIX EFFORT:** LOW

**TITLE:** Agent-authored recommendations record `agent_run_id = NULL` and are displayed as deterministic

**OBSERVED:** After a successful live agent run on the flagship:
```
rec 1  incident 1  agent_run_id = None  has_rationale = True
agent_runs: [(1, 1, 'SUCCEEDED')]

API:  agent_run_id      : None
      rationale         : "Simulation 4 is the strongest permitted option and aligns wi…"
      rationale_truth   : AI_GENERATED
UI Provenance panel: "AGENT RUN — NONE (deterministic)"
```

**EXPECTED:** A recommendation produced by the agent records the agent run that produced
it, and the UI attributes it to the agent.

**IMPACT:** A single API payload simultaneously asserts *no agent was involved*
(`agent_run_id: null`) and *this text is AI-generated* (`rationale_truth: AI_GENERATED`).
The Recommendation screen tells the operator the recommendation is deterministic when an
LLM authored it. This is exactly the provenance confusion the product is built to prevent,
and it is visible on the screen a judge is most likely to scrutinise.

**EXACT REPRODUCTION:** `POST /api/incidents/1/agent/analyze`, then
`GET /api/incidents/1/recommendation` → `agent_run_id: null` while `rationale` is present;
open the Recommendation tab → "AGENT RUN: NONE (deterministic)".

**EVIDENCE:** Above, plus code: `build_recommendation` accepts `agent_run_id` (signature
line 150 of `aventum_action/recommendation.py`) and `recommendations.agent_run_id` exists,
but `aventum_agent/tools.py::_propose_action` never passes it, and `ToolContext`
(`tools.py` line 131) carries `session, incident_id, analysis_run_id, world,
simulations_used` — **no run id to pass**.

**OWNING LAYER:** AGENT (`aventum_agent/tools.py`, `ToolContext`)
**ROOT CAUSE:** Proven. `ToolContext` has no `agent_run_id` field, so `_propose_action`
structurally cannot supply the parameter the recommendation builder already accepts.
Pre-existing Day 4B behaviour; Day 5 surfaced it by rendering provenance.
**RECOMMENDED FIX:** Add `agent_run_id` to `ToolContext`, populate it in the loop where
the run row is created, and pass it through `build_recommendation`. Three lines plus the
agent-suite re-run.
**REGRESSION TEST:** Assert that a recommendation created via `_propose_action` has a
non-null `agent_run_id` matching the run, and that a deterministic
`run_decision_pipeline` recommendation has `agent_run_id IS NULL`.
**STATUS:** OPEN

---

**ID:** AV-04
**AREA:** Reliability / Degraded mode / API
**SEVERITY:** P1 · **DEMO LIKELIHOOD:** LOW-MEDIUM · **REGRESSION RISK:** LOW · **USER IMPACT:** HIGH · **FIX EFFORT:** LOW

**TITLE:** With the database down every endpoint hangs >120 s, including `/api/health`; the UI then blames the API

**OBSERVED:** With `aventum-postgres` stopped and the API process healthy:
```
/api/health        -> HTTP 000 after 120.003s   (curl ceiling, not a response)
/api/overview      -> HTTP 000 (timed out)
/api/incidents/1   -> HTTP 000 (timed out)
/api/batch/recovery-> HTTP 000 (timed out)
```
In the browser: ~30 s of skeleton loaders, then the honest
`The request timed out before the backend responded. TIMEOUT` with Retry — but the
System Health panel reads **API UNREACHABLE**, Database **UNKNOWN**, while the API is
perfectly healthy and the *database* is what failed.

**EXPECTED:** Fail fast. `/api/health` in particular must return 200 with
`database: {ok: false}` — reporting component health is its only job.

**IMPACT:** Two problems. First, a 30-second dead screen is the "no infinite spinner"
requirement in spirit if not letter, and it is a long time in front of a judge. Second,
the operator is told the **wrong component** failed, which is worse than saying nothing:
it would send someone debugging the API while the database is the problem.

**EXACT REPRODUCTION:** `docker compose stop` while the API runs, then
`curl -m 120 http://127.0.0.1:8000/api/health` → no response in 120 s.

**EVIDENCE:** Above. Root cause in `aventum_api/deps.py` line 25:
```python
_engine = create_engine(_config.database_url, pool_pre_ping=True, future=True)
```

**OWNING LAYER:** API (`aventum_api/deps.py`), with a FRONTEND diagnostic-attribution gap.
**ROOT CAUSE:** Proven. No `connect_timeout` is configured on the engine, so acquiring a
connection to a stopped container blocks essentially indefinitely; the health handler's
`try/except` never runs because it never gets a session. The frontend then attributes any
health-call failure to the API because it has no way to distinguish "API down" from "API
up but its dependency is down".
**RECOMMENDED FIX:** Set a short `connect_args={"connect_timeout": 2}` (and a `pool_timeout`)
on the engine so a dead database fails in seconds; make `/api/health` acquire its
connection defensively so it can always answer. Separately, have the frontend report
"API reachable / database unreachable" when it can distinguish them.
**REGRESSION TEST:** With the database stopped, assert `/api/health` returns 200 within
5 s carrying `database.ok == false`.
**NOTE (positive):** The API **self-heals** once the database returns —
`/api/overview` answered in 0.041 s immediately after restart, so `pool_pre_ping` works.
**STATUS:** OPEN

---

## 4. P2 Findings

---

**ID:** AV-05 · **AREA:** API / Concurrency · **P2** · Demo LOW · Regression LOW · Impact MEDIUM · Effort LOW

**TITLE:** Concurrent verification returns HTTP 500 to the losing callers

**OBSERVED:** 5 concurrent `POST /api/actions/1/verify` → codes `[500, 200, 200, 500, 500]`.
Persisted state stayed correct (1 verification row, 1 audit event). Server log:
```
unhandled error on POST /api/actions/1/verify
psycopg.errors.UniqueViolation: duplicate key value violates unique constraint
  "uq_verification_identity"
```
**EXPECTED:** The losing caller receives the stored verdict (200, idempotent) or a clean
409 — never a 500.
**IMPACT:** The database correctly protected state, but the race is unhandled in code, so
the client sees "The request could not be completed." Sequential duplicate verification is
already correctly idempotent; only the race path fails. In the browser double-click test
the error was masked by the winning request's refresh, so it is currently invisible — but
it is a genuine error-contract defect and a logged 500.
**EVIDENCE:** Above; `_audit_api.log` lines 425, 786, 1292.
**OWNING LAYER:** BACKEND (`aventum_verification/verify.py`) — the existence check and the
insert are not atomic.
**ROOT CAUSE:** Proven. Two callers both find no existing verification, both insert, one
loses to `uq_verification_identity`.
**RECOMMENDED FIX:** Catch `IntegrityError` on the verification insert and re-read the
stored verdict, returning it idempotently.
**REGRESSION TEST:** Threaded 5-way verify asserting all responses are 2xx and name the
same `verification_id`.
**STATUS:** OPEN

---

**ID:** AV-06 · **AREA:** Frontend / UX · **P2** · Demo **MEDIUM-HIGH** · Regression LOW · Impact MEDIUM · Effort LOW

**TITLE:** "GMV at Risk" is visually clipped at 1280×800

**OBSERVED:** At 1280×800 the incident header metric renders as `₹70,422.0` — the final
digit is cut off. Measured:
```
span content width 164px inside a 127px parent, in a 159px grid cell
clipped: true   overflowsPanel: true   DOM text: "₹70,422.00"
```
**EXPECTED:** A headline business figure is never truncated at a common laptop resolution.
**IMPACT:** A judge on a 1280×800 or 1366×768 laptop sees a mangled currency figure on the
incident hero screen. The DOM value is correct, so this is presentation only — but it
reads as unfinished, and it is the kind of thing a judge notices immediately.
**EXACT REPRODUCTION:** Open INC-0001 at 1280×800 and read the GMV at Risk tile.
**EVIDENCE:** Measurement above; screenshot captured during the audit.
**OWNING LAYER:** FRONTEND (`incident/tabs/CommandCenter.tsx`, four-column metric grid with
`big` 30px type).
**ROOT CAUSE:** Proven. `md:grid-cols-4` yields ~159 px cells; the `big` variant's 30 px
font cannot fit a 10-character rupee string, and there is no responsive step-down.
**RECOMMENDED FIX:** Reduce the `big` font at narrow widths, or drop to three columns
below ~1400 px, or allow the value to wrap.
**REGRESSION TEST:** Render assertion that no metric value's `scrollWidth` exceeds its
`clientWidth` at 1280 px.
**STATUS:** OPEN

---

**ID:** AV-07 · **AREA:** API contract · **P2** · Demo LOW · Regression LOW · Impact MEDIUM · Effort LOW

**TITLE:** `POST /api/incidents/{id}/agent/analyze` returns HTTP 200 when the agent is unavailable

**OBSERVED:** With Ollama stopped:
```
POST /api/incidents/1/agent/analyze -> HTTP 200 in 4.34s
{ "agent_run_id": null, "status": "AGENT_UNAVAILABLE",
  "final_state": "ABANDONED", "recommendation_id": null }
```
No `agent_runs` row was persisted.
**EXPECTED:** 503 with `AGENT_UNAVAILABLE`, which the handler already has code for.
**IMPACT:** A client branching on HTTP status treats a failed operation as success. The
body is honest, and the UI is currently protected — `/api/health` reports the agent down,
so the panel renders "Unavailable" and does not even show the Run button — so exposure is
limited to the window between health polls (20 s). Also, a failed agent attempt leaves
**no persisted trace at all**, so the audit cannot show that the agent was tried.
**EVIDENCE:** Above; UI confirmed to hide the Run button when health reports agent down.
**OWNING LAYER:** API / AGENT.
**ROOT CAUSE:** Proven. `AgentUnavailable` is caught inside `analyze_incident` and returned
as an outcome, so the route's `except AgentUnavailable → 503` branch is unreachable on
this path.
**RECOMMENDED FIX:** Map `outcome.status == "AGENT_UNAVAILABLE"` to a 503 with the same
error envelope, and consider persisting an `agent_runs` row for the failed attempt.
**REGRESSION TEST:** With the agent disabled, assert 503 and a stable error code.
**STATUS:** OPEN

---

**ID:** AV-08 · **AREA:** Documentation · **P2** · Demo MEDIUM · Regression LOW · Impact MEDIUM · Effort LOW

**TITLE:** `docs/CURRENT_AVENTUM_HANDOFF.md` describes a system two phases old

**OBSERVED:** The file titled **"Current Engineering Handoff"** is dated 2026-08-28 at HEAD
`70b3907` (Day 4 pre-flight) and states:
```
| Counterfactual routing simulation | DESIGNED, NOT IMPLEMENTED |
| Bounded recovery recommendation   | DESIGNED, NOT IMPLEMENTED |
| Human approval                    | DESIGNED, NOT IMPLEMENTED |
| Safe (simulated) execution        | DESIGNED, NOT IMPLEMENTED |
| Verification                      | PLANNED (Day 5), NOT DESIGNED IN DETAIL |
| Auditability                      | DESIGNED, NOT IMPLEMENTED |
| Frontend                          | NOT STARTED |
```
and reports "378 passed" in four places. All seven capabilities now exist; the suite is 602.
**EXPECTED:** A document called "Current" reflects current reality, or is renamed/dated.
**IMPACT:** This is the file a new engineer or a judge is most likely to open first. It
would tell them the product does not have the features being demonstrated.
**EVIDENCE:** Above. `README.md` (602 tests) and the Day 5 report (19 endpoints) are
**correct** — the staleness is confined to this file.
**OWNING LAYER:** Documentation.
**ROOT CAUSE:** Proven — written as a point-in-time extraction on Day 4 pre-flight and
never superseded.
**RECOMMENDED FIX:** Rename to `DAY4_PREFLIGHT_HANDOFF.md` or prepend a superseded-by
banner pointing at `DAY5_IMPLEMENTATION_REPORT.md`.
**STATUS:** OPEN

---

## 5. P3 Findings

| ID | Area | Finding | Evidence |
|---|---|---|---|
| AV-09 | Accessibility | **6 contrast failures.** `--faint-foreground` `rgb(98,107,120)` measures **3.48:1** against surface (needs 4.5:1): "Payment Operations", "System Health", "INC-0001", the `›` step separators, "6 supporting · 0 contradicting". Step-number badge white-on-accent measures **3.25:1**. | Live DOM luminance computation |
| AV-10 | Accessibility | **Heading levels skip H1 → H3** (no H2 anywhere on the incident screen). | `headings: [H1, H3, H3, H3, H3]`, `skippedLevels: true` |
| AV-11 | Accessibility | **7 of 14 SVGs** lack `aria-hidden`, `aria-label` and `<title>`. | Live DOM count |
| AV-12 | Accessibility | The screen-reader table in `GatewayHealthChart.tsx` has a `<caption>` but its `<th>` elements lack `scope`. | `ths: [{scope: null} × 3]` |
| AV-13 | Repository | Empty Day-1 placeholder directories remain: `frontend/`, `agent/`, `simulator/` (each only `.gitkeep`) while the real frontend lives in `razorpay frontend new/`. Reads as unfinished. | `find` output |
| AV-14 | Repository | The frontend directory is named **`razorpay frontend new`** — spaces and "new" in a submitted path. Cosmetic but unprofessional, and the space complicates shell commands. | Path |
| AV-15 | Performance | In `npm run dev`, React StrictMode double-invokes effects so every endpoint is fetched 2–4×. **Confirmed absent in the production build** (`npm run preview`: exactly 4 requests, 0 duplicates). Dev-only. | PerformanceObserver, both builds |
| AV-16 | Testing | **No frontend tests exist and no lint is configured** (`package.json` has no `test` script, no eslint config). Frontend correctness rests entirely on typecheck + manual browser testing. | `package.json`, file listing |
| AV-17 | Build | Alembic emits `DeprecationWarning: No path_separator found in configuration`. | pytest `-W default` |
| AV-18 | Security | `/docs`, `/redoc`, `/openapi.json` are exposed (200). Useful for a judge; an information surface in any other context. | HTTP probes |
| AV-19 | Performance | `/api/health` costs ~2.0 s on the first call of each 15 s cache window (live Ollama probe); 3.6 ms warm. | Latency snapshot |
| AV-20 | Documentation | `docs/DAY5_HANDOFF_MANIFEST.md` does not exist at that path (it is `handoff/5/MANIFEST.md` and `final handoff/docs/DAY5_HANDOFF_MANIFEST.md`). | File check |

---

## 6. Functional Workflow Findings

The full flagship was executed end to end **three times** (API-driven, browser-driven, and
from a clean reset), plus twice more inside the state-machine tests.

| Stage | UI | API | Persisted state | Latency |
|---|---|---|---|---|
| Overview (clean) | 7 incidents, all money `UNAVAILABLE` | `recovery: NO_ACTIVE_ACTION` | 0 workflow rows | 15 ms |
| Incident detail | 9.26σ / 68.81% / 74.03% / ₹70,422.00 | identical | matches `incident_rca_results` | 58 ms |
| Evidence & RCA | 1 PRIMARY, 5 DERIVATIVE | identical | matches `incident_anomalies` | — |
| Simulation | 13 candidates, NO_ACTION baseline shown | identical | 13 `counterfactual_simulations` | 8 ms |
| Recommendation | `REROUTE gateway_C→gateway_A @30%`, ₹19,126.26, PERMITTED | identical | matches `recommendations` | — |
| Approval | PENDING → APPROVED, attributed | identical | `approvals` row | 69 ms |
| Execution | EXECUTED, `SimulatedRoutingAdapter`, `is_simulated=true` | identical | 1 `actions` row | 69 ms |
| Verification | RECOVERY_EFFECTIVE, 20.83%→17.42%, 7/7 integrity | identical | 1 `verifications` row | 19 ms |
| Audit | 7 lifecycle stages present | identical | 19 events | 7 ms |
| Batch | ₹19,126.26 projected vs ₹14,668.00 recovered, uplift 76.7% | identical | counted from rows | 10 ms |

**Reproducible.** Repeated runs from `POST /api/demo/reset` produced identical
recommendation, identical simulation selection, identical verification verdict and
identical batch figures. No nondeterminism was observed in the deterministic path.

**Zero console errors and zero failed network requests** across the entire browser pass.

---

## 7. State Machine Findings

28 adversarial transitions attempted. **All refused correctly.**

| Attack | Result |
|---|---|
| EXECUTE without approval | 409 `NO_APPROVAL` |
| VERIFY nonexistent action | 404 |
| Approval for nonexistent recommendation | 404 |
| Decide nonexistent approval | 404 |
| Duplicate approval request | 409 |
| Approve with no / whitespace identity | 400 `APPROVER_REQUIRED` |
| Forged decision values (`EXECUTED`, `VERIFIED`, `yes`, `""`, `null`, `1`, `true`, `APPROVED;DROP TABLE actions`) | 400 `INVALID_DECISION` (8/8) |
| Injected extra fields (`status`, `approval_id`, `expected_gmv_retained`, `is_simulated`, `recommendation_id`) | **Ignored**; DB row matches the honest response |
| Cross-incident execute using another incident's approval | 409 |
| Policy-blocked recommendation approved | 409 |
| NO_ACTION / blocked recommendation executed | 409 |
| Duplicate execution | Idempotent — same `action_id`, 1 row, original `executed_by` preserved |
| Duplicate verification (sequential) | Idempotent — same `verification_id` |
| Negative / huge / non-numeric incident ids | 404 / 404 / 422 |
| Malformed JSON body | 422 |

**Database-level invariants independently confirmed by SQL:**
```
actions without an approval row                        : 0
verifications without an action row                    : 0
EXECUTED actions whose approval is not APPROVED        : 0
COMPLETE verifications of non-executed actions         : 0
```
Both application checks *and* database constraints hold. The one gap is the approval
decision (AV-02), which has application checks but **no database-level guard** — the only
place in the state machine where the two layers are not both present.

---

## 8. Frontend / UX Findings

- All 6 incident tabs render with correct, honest empty states ("No candidates simulated
  yet", "Nothing to approve", "Nothing to execute").
- **Refresh at every stage reconstructs backend truth.** Verified at: clean overview,
  incident loaded, awaiting approval, approved, executed, verified. After a hard reload
  mid-workflow the Decision State correctly showed *Diagnosed/Simulated/Recommended/Policy
  validated complete, Human approved in progress*.
- **A second browser tab** on the same incident showed identical authoritative state
  (VERIFIED / RECOVERY_EFFECTIVE).
- No refresh or navigation rewound approval, rewound execution, lost verification,
  duplicated an action, or unlocked a locked operation.
- **EXECUTED ≠ VERIFIED is preserved on screen**: after execution the panel states "The
  action has executed, but it has **not** been verified."
- 1440×900: clean. 1280×800: no horizontal page scroll, right rail intact, but see AV-06.
- Defects: AV-06 (clipping), AV-09…AV-12 (accessibility), AV-15 (dev-only duplicate fetches).

---

## 9. API / Backend Findings

19 operations, enumerated from the live OpenAPI. Error envelope is consistent
(`{detail: {code, message}}`), status codes are coherent (404 / 400 / 409 / 422), and no
endpoint leaks SQL, credentials, connection strings or stack traces — verified by scanning
the actual response bodies of all 8 read endpoints for
`postgresql://`, `SELECT `, `INSERT `, `password`, `aventum_local_dev`: **zero hits**.

**No hidden side effects on GET.** An AST pass over every `@router.get` handler found no
session writes and no write-shaped SQL; the one flagged `.add(` is `seen.add()` on a Python
set. All SQL in `app.py` is parameterised. The f-string SQL in `demo.py` interpolates only
module-level constant table names, never user input — acceptable.

Defects: AV-04 (no connect timeout), AV-05 (500 on verify race), AV-07 (200 on agent
failure).

**Observation, not a defect:** `decision` is upper-cased before validation, so `"approved"`
is accepted as `APPROVED`. Case-insensitive input is defensible, but it is undocumented
and the tests assert exact-case values.

---

## 10. Database / Concurrency Findings

5-way barrier-synchronised concurrency against each mutating endpoint:

| Endpoint | Rows created | Audit events | Response codes | Verdict |
|---|---|---|---|---|
| approval-request | **1** | 1 | `[409,409,200,409,409]` | correct |
| approval decision | 1 | **4** | `[200,200,200,409,200]` | **AV-02** |
| execute | **1** | **1** | `[200×5]` | correct |
| verify | **1** | **1** | `[500,200,200,500,500]` | state correct, **AV-05** |

`uq_action_idempotency`, `uq_approval_one_pending` and `uq_verification_identity` all
demonstrably do their jobs. Transaction boundaries are sound: mutating routes commit
before responding, and an immediate follow-up read observes the write
(`POST` then zero-delay `GET` returned `rec 1`, not null).

---

## 11. Agent Findings

Six live `qwen3:8b` runs across five scenario types.

| Scenario | Status | Final state | Turns | Tools | Sims | Context | Wall |
|---|---|---|---|---|---|---|---|
| flagship gateway_C | SUCCEEDED | REQUEST_APPROVAL | 5 | 3 | 0 | 5,920 | 64–69 s |
| issuer/systemic SBI | SUCCEEDED | ASSESS | 1 | 0 | 0 | 5,794 | 16 s |
| mild | SUCCEEDED | ASSESS | 2 | 1 | 0 | 2,354 | 22 s |
| marginal | SUCCEEDED | ASSESS | 4 | 1 | 0 | 5,120 | 62 s |
| systemic fleet-wide | SUCCEEDED | ASSESS | 4 | 0 | 0 | **7,498** | 50 s |

**Budget contract intact.** No run exceeded 12 turns / 20 tool calls / 8 simulations /
180 s. Zero violations.

**Authority boundary held.** Across all runs: 0 chain-of-thought markers in any persisted
field (30 fields scanned across `agent_tool_calls.request/response`,
`recommendations.rationale`, `audit_events.payload`); 0 ground-truth leakage into tool
responses; 0 SQL in tool traffic; 0 agent-requested simulations; the agent never approved,
never executed, and never created a candidate.

**Agent agrees with the deterministic layer 5/5** on live data — including incident 5,
which the deterministic pipeline also resolves to NO_ACTION (0.63σ, severity NONE), so the
agent is correct there rather than regressing.

**Degradation verified:** with Ollama stopped, the complete deterministic flow
(analyze → approve → execute → verify → RECOVERY_EFFECTIVE, integrity true) still
succeeded, and the UI showed "Unavailable / Deterministic incident analysis remains
available" without fabricating a rationale.

**Risk noted:** context reached **7,498 of the 8,000** `MAX_CONTEXT_TOKENS` budget on the
systemic incident — 94% of budget, ~6% headroom. Not a defect today; a larger incident
context would trip the guard. Recorded as a limitation, not a finding.

Defects: AV-03 (provenance), AV-07 (status code).

---

## 12. Security Findings

**No P0/P1 security defect found.**

| Check | Result |
|---|---|
| `eval` / `exec` / `subprocess` / `os.system` / `pickle.loads` / `__import__` / `shell=True` in shipped packages | **none** (single grep hit is a docstring stating their absence) |
| Dynamic SQL from user input | **none** — all parameterised; `demo.py` interpolates constant table names only |
| Credentials / connection strings in responses | **none** across all read endpoints |
| Stack traces or internal exceptions in responses | **none** — global handler returns `{code: INTERNAL_ERROR, message}` and logs server-side |
| CORS | closed 4-origin allow-list, not `*` |
| Forged IDs, cross-incident IDs | refused (404/409) |
| Numeric injection into approval/verification | **ignored** — server values preserved |
| Unexpected JSON fields | **ignored** |
| SQL-shaped values (`APPROVED;DROP TABLE actions`) | rejected as invalid enum |
| Extreme / negative / null values | 404 / 422, no 500 |
| Malformed JSON | 422 |
| Client-supplied verification values (`outcome`, `gmv_recovered`, `attainment_ratio`) | **ignored** — server recomputed |

No authentication exists — the approver identity is typed, not authenticated. This is a
**known and documented limitation** appropriate to a demo, not a defect, but it means the
approval trail is attributable only to what someone typed.

---

## 13. Data / Provenance Findings

**21 of 21 headline values matched exactly between API and database.** Checked:
significance, confidence, evidence strength, detection GMV at risk, expected GMV retained,
expected success delta, risk score, traffic percentage, policy result, approval status and
approver, action status and adapter, verification baseline/actual failure rates, GMV
recovered, attainment, transactions moved, outcome, and both batch money totals.

**8 of 8 provenance separations hold:**
- projected GMV (₹19,126.26) ≠ actual recovered GMV (₹14,668.00) — separate quantities
- `projected.truth == PROJECTED`, `actual_simulated.truth == SIMULATED`,
  `measured.truth == VERIFIED`, `baseline.truth == SIMULATED`
- gateway health tagged `SYNTHETIC`; capacity `UNAVAILABLE` everywhere
- success and failure rates carried independently (sum to 1.0 but neither is inferred)

**Observed-data immutability confirmed** across three consecutive demo resets: transactions
250,000; canonical and generation fingerprints unchanged; incidents 7; RCA 8; evidence 264;
anomalies 117; simulated outcomes 14,651; ground truth 7; synthetic gateways 5 — **all
unchanged**. Synthetic, simulated and observed layers remain separate.

The one provenance defect is AV-03, where the agent/deterministic attribution is wrong.

---

## 14. Approval / Execution Findings

**Approval.** Server-side validation decides everything; no frontend state is trusted.
Missing identity, whitespace identity, forged decision values, injected fields, duplicate
requests, decisions on already-decided approvals and cross-incident approvals were all
refused. Approval artifacts are persisted with the payload the human actually saw.
**Defect: AV-02** (duplicate audit events).

**Execution.** `SimulatedRoutingAdapter` is the only adapter; `is_simulated` is `true` and
database-enforced. Execution requires a granted approval, refuses without one, is
idempotent under 5-way concurrency, preserves the original `executed_by` against a forged
duplicate, and emits exactly one `ACTION_EXECUTED` event. **No production execution path
is reachable.** No defect found.

---

## 15. Verification Findings

**This is the strongest area of the product.** Independence was tested rather than assumed.

**It can genuinely fail, on merit, with integrity passing.** Constructing internally
consistent bad outcomes (correct failure counts *and* a recomputed valid execution
fingerprint) so that integrity passes and the verdict must rest on the numbers alone:

| Constructed outcome | Integrity | Verdict |
|---|---|---|
| 54/264 failures — tiny gain, 11% of projection | **PASSES** | `RECOVERY_NOT_VERIFIED` |
| 49/264 failures — 67% of projection | **PASSES** | `PARTIALLY_EFFECTIVE` |
| 70/264 failures — worse than baseline | **PASSES** | `RECOVERY_NOT_VERIFIED` |
| genuine 46/264 (control) | PASSES | `RECOVERY_EFFECTIVE` |

All three grades are reachable, and a real improvement that badly missed its projection is
correctly **not** certified.

**Integrity catches tampering.** Rewriting `actual_simulated_outcome` without recomputing
the fingerprint produced `integrity_passed: false` and `RECOVERY_NOT_VERIFIED` — the
execution-fingerprint recomputation detected the edit.

**It is independent by construction.** An AST import scan confirms neither
`aventum_verification/constants.py` nor `verify.py` imports `aventum_policy` or
`aventum_action.recommendation`. Client-submitted `outcome`, `gmv_recovered`,
`attainment_ratio` and `integrity_passed` are ignored.

**Failure propagates correctly** to DB (`outcome = RECOVERY_NOT_VERIFIED`), audit
(`VERIFICATION_COMPLETED` payload carries the failure), API, UI and batch.

Defect: AV-05 (concurrency error contract) — does not affect correctness of any verdict.

---

## 16. Batch Measurement Findings

Every figure is counted from persisted rows; both money totals were cross-checked against
direct SQL aggregates and matched exactly.

**Honesty invariants verified by execution:**
- The two money figures are **never summed** — served as separate fields with separate
  truth tags, and the UI renders them side by side under distinct labels.
- **Unverified recovery contributes zero.** With a `RECOVERY_NOT_VERIFIED` action,
  `total_actual_gmv_recovered == 0.0` while `recovery_not_verified_count == 1`, even
  though the underlying `actual_gmv_recovered` was −41,086.
- **Empty population is `UNAVAILABLE`, not 0%.** On a clean database
  `verification_success_rate`, `recovery_uplift` and `intervention_rate` all return the
  string `UNAVAILABLE`; after a genuine failure `verification_success_rate` becomes `0.0`.
  The distinction between "not tried" and "tried and failed" is preserved.
- No estimated value is presented as measured.

No defect found.

---

## 17. Audit Findings

Reconstructed **from the database**, not the UI. The flagship produces:
```
13× SIMULATION_COMPLETED -> POLICY_VALIDATED -> RECOMMENDATION_CREATED
 -> APPROVAL_REQUESTED -> APPROVAL_DECIDED (HUMAN:<identity>)
 -> ACTION_EXECUTED (HUMAN:<identity>) -> VERIFICATION_COMPLETED (AVENTUM_VERIFICATION)
```
All 7 required lifecycle stages present; `event_id` ordering matches lifecycle ordering;
`output_ref` values resolve to real rows (`recommendations`, `approvals`, `actions`,
`verification_id`); fingerprints attached; the human decision attributed to a person, the
verification to the deterministic verifier. No chain-of-thought in any payload.

Two defects: **AV-02** (duplicate `APPROVAL_DECIDED`) and, under concurrency only,
`event_id` order not matching `occurred_at` order. Append-only holds — no UPDATE or DELETE
path is exposed and retries add rows rather than rewriting them.

---

## 18. Failure / Degraded Mode Findings

| Injected failure | What the operator sees | Verdict |
|---|---|---|
| Backend unavailable | `Cannot reach the Aventum backend…` + `NETWORK_UNREACHABLE` + Retry; health shows API UNREACHABLE | **correct** |
| **Database unavailable** | 30 s skeleton, then `TIMEOUT` + Retry — but blames the **API** | **AV-04** |
| Ollama unavailable | "Agent Unavailable / Deterministic incident analysis remains available"; Run button hidden; full deterministic flow still works | **correct** |
| Network timeout | `TIMEOUT` after the client budget | correct |
| Malformed API response | `MALFORMED_RESPONSE` raised by the client | correct |
| Malformed JSON request | 422 | correct |
| Stale recommendation | `is_stale: true` with reasons and a next step; not executable | correct |
| Expired approval | 409 | correct |
| Rejected approval | Stop panel: what happened, why, what next | correct |
| Rejected execution | Stop panel with the backend's reason | correct |
| Failed verification | `RECOVERY_NOT_VERIFIED` shown as prominently as success | correct |
| Empty audit / empty incidents / missing metadata | explicit empty states, never fabricated | correct |

No false success and no fabricated data in any degraded mode. The only failure that does
not answer *what happened / why / what next* correctly is the database outage (AV-04).

---

## 19. Performance Findings

| Endpoint | Latency |
|---|---|
| `/api/overview` | 15 ms |
| `/api/incidents/1` | 58 ms |
| `/api/incidents/1/simulations` | 8 ms |
| `/api/incidents/1/audit` | 7 ms |
| `/api/batch/recovery` | 10 ms |
| `/api/health` | 3.6 ms warm / ~2.0 s on the first call per 15 s window |
| `POST analyze` | 613 ms |
| `POST execute` | 69 ms |
| `POST verify` | 19 ms |
| Agent run | 16–69 s |

Frontend: build 242 ms, bundle 291.95 kB (82.42 kB gzip). Production build issues exactly
4 requests when opening an incident, **zero duplicates**. No blocking calls, no excessive
polling (polling exists only while an action is executed-but-unverified and stops when
that clears), no layout shift observed.

Defects: AV-15 (dev-only duplicate fetches), AV-19 (health probe cost).

---

## 20. Accessibility Findings

Measured in the live DOM, not assumed.

**Passing:** exactly one `h1`; no button without an accessible name; no positive
`tabindex`; landmarks present (`main`, `nav`×2, `aside`×2, `header`); visible focus ring
defined; state communicated by shape as well as colour in System Health (circle vs square);
tables carry captions; `role="status"` / `role="alert"` used on loading and error states.

**Failing:** AV-09 (6 contrast failures, worst 3.25:1), AV-10 (heading skip H1→H3),
AV-11 (7/14 SVGs unlabelled), AV-12 (`th` without `scope` in the chart's sr-only table).

All four are P3 for a buildathon demo.

---

## 21. Deployment / Reproducibility Findings

The documented runbook works, with two caveats.

Verified: `docker compose up -d` → `alembic upgrade head` → `uvicorn aventum_api.app:app`
→ `npm run dev`, then `POST /api/demo/reset` and the full flagship — **no manual SQL, no
fixture edits, no console manipulation, no undocumented steps.**

Caveats:
1. The runbook assumes the Docker **daemon** is already running. `docker compose up -d`
   fails with a pipe error if Docker Desktop is not started, and nothing says so. (P3)
2. AV-01 — the repository does not currently contain the product, so the runbook cannot be
   followed from a clean clone at all.

Repository hygiene is fine: 285 tracked files, 79.79 MiB packed. The 231 MB `audit_scripts/`
and 308 MB `.venv` are **gitignored** and never shipped — not a defect.

---

## 22. Documentation Findings

**Correct:** `README.md` (602 tests), `DAY5_IMPLEMENTATION_REPORT.md` (19 endpoints),
`handoff/5/MANIFEST.md` and `final handoff/MANIFEST.md` — all match live reality, including
the defect lists, which record the Day 5 fixes honestly.

**Defects:** AV-08 (`CURRENT_AVENTUM_HANDOFF.md` two phases stale) and AV-20 (a doc path
referenced by this audit's own brief does not exist).

**Observation:** the Day 4B P1 report's scenario labels (`C_mild → REROUTE 10/10`) do not
map onto the live database's incident IDs — live incident 5 is a genuine NO_ACTION case.
The trial ran against a differently-seeded database. Not a product defect, but a
traceability trap for anyone re-running those numbers.

---

## 23. Demo / Judge Risks

Acting as a first-time judge, all ten questions are answerable in seconds:

| Question | Answered by |
|---|---|
| What happened? | Incident header: `golden-gateway-c-degradation · gateway_C`, CRITICAL |
| Why? | Root Cause panel + Evidence & RCA, 1 PRIMARY / 5 DERIVATIVE |
| How serious? | 9.26σ, CRITICAL, ₹70,422.00 at risk |
| What does Aventum recommend? | `REROUTE gateway_C→gateway_A @30%` |
| Why permitted? | Policy panel: PERMITTED + thresholds satisfied |
| Who approved? | Approval panel: identity, timestamp, fingerprint |
| What happened after execution? | Execution panel: adapter, fingerprint, `is_simulated` |
| Did it work? | Verification: RECOVERY_EFFECTIVE, 20.83%→17.42% |
| Projected vs measured? | Side by side, differently tagged, ₹19,126.26 vs ₹14,668.00 |
| What is the AI doing? | Agent panel: tool activity, budgets, rationale — clearly subordinate |

**Risks ranked by likelihood of surfacing during a live demo:**

1. **AV-01** — if a judge clones the repo, there is no product. *Certain if they clone.*
2. **AV-06** — clipped GMV figure on a 1280×800 laptop. *Likely on smaller screens.*
3. **AV-02** — a nervous double-click on Approve corrupts the audit count. *Plausible.*
4. **AV-03** — a judge who notices "AGENT RUN: NONE (deterministic)" beside an AI-generated
   rationale will ask about it, and the honest answer is that the data is wrong.
5. **AV-04** — only if the database dies mid-demo, but the 30-second dead screen would be
   painful and the diagnosis misleading.
6. Agent latency: 16–69 s per run. Correctly signposted ("45–90 seconds on a local 8B
   model") but it is dead air in a timed demo. *Consider pre-running the agent.*

**Terminology risks:** none found. "Simulation Mode" is persistent in two places; capacity
is explicitly `UNAVAILABLE`; no production-recovery claim appears anywhere; the agent is
never presented as authoritative.

---

## 24. Health Scorecard

| Area | Status | Severity | Demo Risk | Key Finding |
|---|---|---|---|---|
| Data integrity | **STRONG** | — | LOW | Fingerprints and 250,000 rows unchanged across 3 resets |
| Provenance | **DEFECT** | P1 | MEDIUM | AV-03: agent-authored recommendation labelled deterministic |
| Backend | **STRONG** | P2 | LOW | AV-05 verify race returns 500 |
| API | **GOOD** | P1/P2 | MEDIUM | AV-04 no connect timeout; AV-07 200 on agent failure |
| Database | **STRONG** | P1 | MEDIUM | Constraints excellent; approval decision lacks one (AV-02) |
| Agent | **STRONG** | P1 | MEDIUM | Budgets intact, boundary held; AV-03 provenance |
| Frontend | **GOOD** | P2 | MEDIUM-HIGH | AV-06 clipped GMV at 1280×800 |
| State machine | **STRONG** | — | LOW | 28/28 adversarial transitions refused |
| Security | **STRONG** | — | LOW | No injection, no leakage, no unsafe primitive |
| Approval | **GOOD** | P1 | MEDIUM | Authority sound; AV-02 duplicate audit events |
| Execution | **STRONG** | — | LOW | Idempotent, simulated-only, no production path |
| Verification | **STRONG** | P2 | LOW | Genuinely independent, all 3 verdicts reachable |
| Batch measurement | **STRONG** | — | LOW | All honesty invariants hold |
| Audit | **GOOD** | P1 | MEDIUM | Complete and traversable; AV-02 duplicates |
| Performance | **STRONG** | P3 | LOW | 7–58 ms reads; dev-only duplicate fetches |
| Accessibility | **WEAK** | P3 | LOW | Contrast, headings, SVG labels |
| Deployment | **BLOCKED** | P1 | HIGH | AV-01 product not committed |
| Demo | **GOOD** | P1 | MEDIUM | Reproducible; risks above |
| Documentation | **GOOD** | P2 | MEDIUM | AV-08 stale "current" handoff |

---

## 25. P0 — Fix Immediately

**None.**

---

## 26. P1 — Fix Before Demo

1. **AV-01** — Commit and push Day 4B + Day 5. *(Effort: LOW. Without this nothing else matters.)*
2. **AV-02** — Serialise the approval decision server-side; stop duplicate `APPROVAL_DECIDED` events. *(LOW)*
3. **AV-03** — Thread `agent_run_id` through `ToolContext` → `_propose_action` → `build_recommendation`. *(LOW)*
4. **AV-04** — Add `connect_timeout` to the engine; let `/api/health` report a down database. *(LOW)*

---

## 27. P2 — Fix If Time

5. **AV-06** — Stop the GMV figure clipping at 1280×800. *(LOW)*
6. **AV-05** — Catch `IntegrityError` on the verification insert; return the stored verdict. *(LOW)*
7. **AV-08** — Rename or banner `CURRENT_AVENTUM_HANDOFF.md`. *(LOW)*
8. **AV-07** — Return 503 when the agent is unavailable. *(LOW)*

---

## 28. P3 — Polish

AV-09 contrast · AV-10 heading levels · AV-11 SVG labels · AV-12 `th scope` ·
AV-13 empty placeholder directories · AV-14 folder name · AV-15 dev duplicate fetches ·
AV-16 no frontend tests or lint · AV-17 Alembic warning · AV-18 `/docs` exposure ·
AV-19 health probe cost · AV-20 doc path.

---

## 29. Top 10 Fixes

| # | Issue | Why it matters | Effort | Expected benefit |
|---|---|---|---|---|
| 1 | **AV-01** commit + push | The submitted artifact currently does not contain the product | LOW | Makes submission possible at all |
| 2 | **AV-02** approval serialisation | The audit trail can misreport how many times a human decided — the product's core claim | LOW | Audit integrity under the most likely user action (double-click) |
| 3 | **AV-03** agent provenance | One payload says both "no agent" and "AI-generated"; visible on the Recommendation screen | LOW | Removes a self-contradiction in the product's central thesis |
| 4 | **AV-04** DB connect timeout | A database outage produces a 30 s dead screen and blames the wrong component | LOW | Fast, correct failure diagnosis |
| 5 | **AV-06** GMV clipping | Mangled headline currency on a common laptop resolution | LOW | Removes the most likely "looks unfinished" impression |
| 6 | **AV-05** verify race | Logged 500s and a possible scary error on double-click | LOW | Clean error contract; matches execute's behaviour |
| 7 | **AV-08** stale handoff doc | The file a newcomer opens first says the product is unimplemented | LOW | Documentation stops contradicting the product |
| 8 | **AV-07** agent 503 | Failed operation reports HTTP success | LOW | Correct API contract; enables honest client handling |
| 9 | **AV-13/14** repo tidiness | Empty `frontend/`, `agent/`, `simulator/`; `razorpay frontend new` | LOW | Repository reads as finished work |
| 10 | **AV-09…12** accessibility | Measured WCAG AA failures | MEDIUM | Credibility if accessibility is assessed |

---

## 30. What Is Already Strong

Verified by execution, not assumed:

- **Verification independence.** Owns its thresholds, imports nothing from the policy or
  recommendation layers (AST-verified), reaches all three verdicts on merit with integrity
  passing, detects tampering via fingerprint recomputation, and ignores client-supplied
  values. This is the best-engineered part of the system.
- **The authority chain.** 28 adversarial transitions, all refused. Policy cannot be
  bypassed, approval cannot be forged, execution cannot precede approval, and the browser
  is treated as untrusted throughout.
- **Idempotency where it exists.** `uq_action_idempotency` and `uq_verification_identity`
  both held under 5-way concurrency.
- **Observed-data immutability.** Three consecutive resets left all ten protected
  quantities byte-identical.
- **Provenance separation of money.** Projected and measured are distinct fields, distinct
  tags, distinct UI labels, never summed; unverified recovery contributes zero; empty
  populations report `UNAVAILABLE` rather than 0%.
- **Refresh and multi-tab correctness.** Business state is reconstructed from the backend
  at every stage; a second tab agrees.
- **Agent boundary and degradation.** Budgets intact, no chain-of-thought, no ground truth,
  no SQL, 5/5 agreement with the deterministic layer, and the entire product works with
  the agent switched off.
- **Error hygiene.** No SQL, credentials, or stack traces reach the browser in any tested
  path, including forced 500s.

---

## 31. What Must NOT Be Changed

Do not modify these without evidence of a specific defect. Each was tested in this audit
and is working:

- `transactions` and the canonical fingerprint `12dec963…f4b8`
- The generation fingerprint `e8414edd…2fe3c8` and the synthetic baseline
- Day 3 Approach B, and the PRIMARY/DERIVATIVE alert-role distinction
- Day 4A deterministic authority and the 13-gate policy
- The mandatory human approval step and its refusal semantics
- `SimulatedRoutingAdapter` as the only adapter, and the `is_simulated` CHECK constraints
- Verification's independence: its own thresholds, its own module, no policy imports
- Ground-truth isolation from the agent
- Agent budgets: 12 turns / 20 tool calls / 8 simulations / 180 s / 8,000 context tokens,
  and `QWEN_NUM_CTX = 9216` with its import-time assertion
- The provenance model and its truth vocabulary
- Backend-owned state derivation (`_recovery_state` computed per request)
- `uq_action_idempotency`, `uq_approval_one_pending`, `uq_verification_identity`
- The demo reset allow-list

Specifically: **do not "fix" AV-02 by disabling the button in React alone.** The server must
be the guard; a frontend-only fix would leave the API defect and create false confidence.

---

## 32. Final Readiness

### HIGH-RISK — FIX BEFORE SUBMISSION

Four confirmed P1 defects remain. None is a P0: nothing corrupts observed data, produces
an unsafe action, bypasses a safety control, or makes a false financial claim. The core
workflow is genuinely reliable and reproducible.

But the repository does not contain the product (AV-01), the audit trail can be made to
misreport a human decision by double-clicking (AV-02), the UI attributes an AI-authored
recommendation to the deterministic layer (AV-03), and a database outage misdiagnoses
itself as an API outage after thirty seconds of blank screen (AV-04).

All four are low-effort fixes. With them addressed — and ideally AV-06 as well — this
would be **READY TO FIX — NO BLOCKING DEFECTS**.
