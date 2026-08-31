_Aventum internal report — Day 5 productization, frontend integration, end-to-end verification._

# Day 5 Implementation Report

Aventum is now one coherent product: a React operations console talking to a real HTTP
API, over the deterministic spine built on Days 1–4, with two capabilities that did not
exist before today — **independent post-action verification** and **batch recovery
measurement**.

The flagship workflow runs end to end through real services and persisted state, with no
fixture data on any authoritative path, no manual database intervention, and no
browser-console manipulation.

---

## Executive Summary

| | Before Day 5 | After Day 5 |
|---|---|---|
| HTTP API | **none existed** | 19 endpoints, the browser's only surface |
| Verification | **did not exist** — Day 4A recorded `"recovery_claim": "NONE — Day 5 owns verification"` | independent deterministic verifier, own thresholds, can return `RECOVERY_NOT_VERIFIED` |
| Batch measurement | did not exist | population-level counts and metrics, all from persisted rows |
| Frontend data | 304 lines of fixtures in `src/lib/data.ts` | **fixtures deleted**; every value comes from the backend |
| Workflow state | local React booleans `{approved, executed, verified}` | derived server-side from persisted rows on every request |
| Demo reset | manual SQL | one endpoint, allow-listed tables, canonical data untouchable |
| Migrations | head `0006` | head `0007` (one additive table) |
| Tests | 545 | **602** (+57 Day 5, none deleted or weakened) |

**Flagship result:** `gateway_C → gateway_A @ 30%`, projected ₹19,126.26, executed through
`SimulatedRoutingAdapter`, independently verified `RECOVERY_EFFECTIVE` — failure rate
20.83% → 17.42%, 79 transactions moved, **₹14,668.00 actually recovered** against
₹19,126.26 projected, attainment 100%, all seven integrity checks passing.

---

## Day 5 System Contract

Built before implementation, per §4. Every screen names its data, its backend source, its
endpoint, its state owner and its failure states.

| Screen | Required data | Backend source | Endpoint | Mutation | State owner | Refresh rule | Failure states |
|---|---|---|---|---|---|---|---|
| Overview | incidents, RCA headline, recovery state, batch metrics | `incidents`, `incident_rca_results`, `aventum_verification.batch` | `GET /api/overview` | — | backend | on mount; after any mutation | network, malformed, empty list |
| Incident | incident, detections, evidence, RCA, gateway health | Day 3 `build_handoff` + `load_world_state` | `GET /api/incidents/{id}` | — | backend | on mount, on incident change | 404, network, health unavailable |
| Simulation | NO_ACTION baseline, candidates, identity, assumptions | `counterfactual_simulations` | `GET /api/incidents/{id}/simulations` | `POST /api/incidents/{id}/analyze` | backend | after analyze | empty (not yet simulated), invalid candidates |
| Recommendation | persisted recommendation, policy verdict, alternatives, staleness | `recommendations` + re-derived fingerprint | `GET /api/incidents/{id}/recommendation` | — | backend | after any mutation | none, blocked, stale, expired |
| Approval | approval row, payload artifact, expiry | `approvals` | same bundle | `POST …/approval-request`, `POST /api/approvals/{id}/decision` | backend | after decision | not requested, pending, rejected, expired, duplicate |
| Execution | action row, pre/expected/actual outcomes | `actions` | same bundle | `POST …/execute` | backend | poll while `VERIFYING` | no approval, rejected at revalidation, duplicate |
| Verification | verdict, measured vs projected, integrity checks | `verifications` | `GET/POST /api/actions/{id}/verify` | `POST` | backend | after verify | not yet verified, ineligible, not verified, integrity failed |
| Audit | persisted events, lifecycle completeness | `audit_events` | `GET /api/incidents/{id}/audit` | — | backend | on mount, manual refresh | empty, incomplete lifecycle |
| Agent activity | agent run, tool calls, budgets | `agent_runs`, `agent_tool_calls` | `GET /api/incidents/{id}/agent` | `POST …/agent/analyze` | backend | after run | not run, unavailable, failed |

### Two defects found after the report was first written

Both were found by running the product rather than reading it, and both are recorded here
rather than quietly folded into the code.

**Mutations committed after the response was sent.** `get_session` commits in a FastAPI
yield-dependency's cleanup, which runs *after* the response goes out. A client that
re-read the instant a mutation returned observed pre-commit state. It surfaced as the
agent panel: a run would succeed, be persisted, and the panel would still report "no agent
run exists for this incident". The row was real; the immediate re-read could not see it
yet. All eight mutating routes now commit before returning, so read-after-write holds.

**The agent panel contradicted itself while working.** During the ~60 s a local 8B model
takes, it rendered a "NOT RUN" pill, the sentence "No agent run exists for this incident",
and a disabled "Agent analysing…" button at the same time. Three disagreeing signals and
no indication of progress read as a hang. It now shows a RUNNING pill, a live elapsed
counter, and a stated expectation.

---

## Frontend Architecture

React 19 + Vite 8 + Tailwind v4, the stack the Figma-derived source already used. No
framework was introduced and no component library was added.

```
src/
  lib/types.ts        domain model, backend's own names
  lib/api.ts          the single door to the backend — one fetch, one error type
  lib/hooks.ts        useResource / useMutation / usePolling
  lib/format.ts       presentation formatting ONLY, no arithmetic that decides anything
  lib/recovery.ts     backend state string -> label, tone, step progress
  components/         design-system primitives (unchanged), states, gateway chart
  incident/           workspace + six workflow tabs
  screens/            Overview, Audit
```

Four layers, one direction: `screens → hooks → api → backend`. No component calls `fetch`,
constructs a URL, or sees a status code.

---

## Figma → React Translation

**The visual language is preserved.** The design tokens in `index.css` are untouched: the
same near-black grounds, the same single restrained accent, the same typography, and — the
part that mattered most — the same **truth-category hues**. The Figma source had already
encoded `--observed`, `--synthetic`, `--simulated`, `--projected`, `--verified`, `--agent`,
`--deterministic` and `--human` as first-class colours, which is exactly the §9 truth model.
That palette drove the integration rather than being reworked around it.

Four changes were made, each for correctness rather than taste:

1. **The seven-day health timeline was replaced** with a gateway failure-probability chart.
   The timeline was a fabricated series with no backend behind it and no endpoint that could
   make it real. §5 is explicit that fake behaviour must not survive because it demos well.
   The replacement shows baseline versus under-incident failure probability per gateway,
   both computed by the counterfactual engine's own `runtime_profile_for`.

2. **Metric headers now wrap instead of truncating.** In a five-across strip the column is
   narrower than "SIGNIFICANCE" plus a "DETERMINISTIC" tag, and the tag was rendering as
   "DETERMINIS…". A half-rendered provenance label is worse than a second line.

3. **The demo "toggle agent unavailable" button was removed.** Agent availability is now a
   real health probe. A button that fakes an outage has no place next to one that reports a
   real one.

4. **System Health rows report three states, not one.** The prototype hard-coded four green
   "OK" rows. They now show OK / DOWN / CHECKING from the real probe, with shape as well as
   colour so state is not communicated by hue alone.

---

## Domain Model

Typed in `src/lib/types.ts` using the **backend's own field names** — `incident_id`,
`analysis_run_id`, `simulation_id`, `recommendation_fingerprint`. A second vocabulary in
React would be a second place for a concept to drift.

`Incident · Detection · Evidence · Rca · GatewayHealth · Simulation · Recommendation ·
Staleness · Approval · Action · Verification · IntegrityCheck · AuditEvent · AgentRun ·
AgentToolCall · BatchRecovery · RecoveryState`

Two modelling decisions carry weight:

- **`Maybe<T> = T | null`.** A value the backend could not supply stays null all the way to
  the renderer, which prints `UNAVAILABLE`. There is no `?? 0` anywhere in the data path.
- **Verification splits into `baseline` / `projected` / `actual_simulated` / `measured`** as
  four separate objects with four separate `truth` labels. A component physically cannot
  render a projection under a measurement's label, because it is never handed one merged
  object.

---

## API Layer

19 endpoints. Every one is a thin translation over an existing deterministic module; the
API contains no thresholds, no money arithmetic, and no policy evaluation of its own.

```
GET  /api/health                                   API, database and agent, reported separately
GET  /api/overview                                 incidents, recovery state, batch metrics
GET  /api/incidents/{id}                           Day 3 truth + gateway health
GET  /api/incidents/{id}/simulations               bounded candidate space
GET  /api/simulations/{id}                         one candidate, full provenance
POST /api/incidents/{id}/analyze                   DETERMINISTIC decision pipeline
GET  /api/incidents/{id}/recommendation            recommendation + approval + action + verification
POST /api/recommendations/{id}/approval-request    raise a human approval
POST /api/approvals/{id}/decision                  record a human decision
POST /api/approvals/expire-stale                   sweep lapsed approvals
POST /api/recommendations/{id}/execute             SimulatedRoutingAdapter, after revalidation
GET  /api/actions/{id}                             action + verification
POST /api/actions/{id}/verify                      independent verification
GET  /api/actions/{id}/verification                persisted verdict
GET  /api/batch/recovery                           population-level measurement
GET  /api/incidents/{id}/audit                     persisted event chain
GET  /api/incidents/{id}/agent                     agent run + tool calls
POST /api/incidents/{id}/agent/analyze             run the Day 4B agent
POST /api/demo/reset                               deterministic demo reset
```

**Provenance labels are assigned server-side.** Every payload names its own truth category.
If React decided which label a number carries, the truth model would live in the layer least
able to defend it.

---

## State Ownership

The backend owns business truth. `RecoveryState` is **derived from persisted rows on every
request** and is never a stored status column:

```
verification exists      -> VERIFIED (carries the outcome)
action EXECUTED          -> VERIFYING
action REJECTED          -> EXECUTION_REJECTED
NO_ACTION recommendation -> NO_ACTION
policy != PERMITTED      -> POLICY_BLOCKED
approval PENDING         -> AWAITING_APPROVAL
…
```

It is computed **backwards from the furthest-progressed artefact**. Reading forwards would
report "awaiting approval" for an incident already executed and verified.

React state is confined to UI concerns: selected tab, expanded row, form draft, disclosure
toggles. The prototype's `FlowState {approved, executed, verified}` is gone; those were
authoritative business booleans that a refresh silently rewound.

---

## Verification

The Day 5 capability the product was missing. Day 4A ends by refusing to make a recovery
claim and hands the question over through `build_verification_handoff`.

### What makes it independent

Independence is not a claim about running the physics twice. It is a claim about **who owns
the standards** and **whether the answer can come back negative**:

1. **Different inputs.** The recommendation was authorised from the simulation summary.
   Verification measures the *adapter's* post-action population against the *execution-time*
   baseline. The adapter re-derives its numbers from the projected outcome population rather
   than echoing the simulation, so the two can genuinely disagree.
2. **Different thresholds, owned here.** `aventum_verification/constants.py` imports nothing
   from `aventum_policy` or the recommendation layer — asserted by a test that parses the
   module's imports rather than grepping its prose.
3. **It can say no.** `RECOVERY_NOT_VERIFIED` is reachable from a successfully executed
   action. An attainment ratio below 20% of the projection produces it *even when the raw
   movement was positive*, because a projection missed that badly means the model that
   authorised the action did not describe reality.
4. **Integrity before merit.** Seven checks re-walk the lineage and recompute the execution
   fingerprint. A number whose provenance does not hold up is never graded on how good it
   looks.

### Flagship verification

| | Value | Provenance |
|---|---|---|
| Baseline failure rate | 20.83% | SIMULATED (execution-time) |
| Actual failure rate | **17.42%** | SIMULATED (adapter) |
| Projected Δ success | +3.41 | PROJECTED |
| Measured Δ success | **+3.41** | VERIFIED |
| Variance vs projection | +0.00 | VERIFIED |
| Attainment | **100%** | VERIFIED |
| Projected GMV retained | ₹19,126.26 | PROJECTED |
| **Actual GMV recovered** | **₹14,668.00** | VERIFIED |
| Transactions moved | 79 | VERIFIED |
| Outcome | **RECOVERY_EFFECTIVE** | DETERMINISTIC |

Note ₹19,126.26 ≠ ₹14,668.00. They are different quantities measured at different moments
and are never summed or substituted — a property asserted by test, not just by convention.

---

## Batch Recovery Measurement

One incident that went well is an anecdote. `GET /api/batch/recovery` aggregates the whole
persisted population, counted from rows and never estimated.

Counts: incidents evaluated · interventions proposed · **NO_ACTION** · **policy blocked** ·
approvals requested/granted/rejected/expired · executed · rejected · verified ·
effective/partial/not-verified.

Metrics: total projected GMV retained · total actual simulated GMV recovered · recovery
uplift · verification success rate · intervention rate · NO_ACTION rate · transactions moved.

Three honesty properties:

- **The two money figures are never summed.** One is a projection over recommendations, the
  other a measurement over verified actions.
- **`RECOVERY_NOT_VERIFIED` contributes zero recovered GMV.** Counting it would inflate the
  headline with money the system just finished saying it could not confirm.
- **An empty population returns `UNAVAILABLE`, not 0%.** "We have not tried yet" and "we
  tried and failed" are different claims.

---

## Stopping / Escalation

Five terminal stops are first-class states, rendered with what happened, why, and what next:
`NO_ACTION` · `POLICY_BLOCKED` · `APPROVAL_REJECTED` · `APPROVAL_EXPIRED` ·
`EXECUTION_REJECTED`. They use a visually distinct treatment from errors, because a stop is
the product working correctly by refusing.

There are no retry loops. A stale recommendation offers re-analysis; it never offers
execution.

---

## Agent Integration

The right rail shows what the agent **did** — tool calls, outcomes, latencies, budget
consumption — and its final rationale. It is not a chat window and it displays no
chain-of-thought; with `think:false` in Day 4B, none is produced to display.

**Agent unavailable degrades safely and was verified live.** With Ollama stopped, the entire
flagship workflow — analysis, simulation, policy, approval, execution, verification, audit —
completed end to end. The panel states the outage and says plainly that deterministic
analysis is unaffected. No explanation, recommendation, confidence or outcome is fabricated.

---

## Audit and Audit Completeness

The Audit screen loads persisted events and shows a **lifecycle completeness strip**
computed from what exists. Missing stages render greyed rather than hidden — a timeline that
always looks complete is worthless as an audit.

The flagship chain, asserted by test:

```
SIMULATION_COMPLETED ×13 → POLICY_VALIDATED → RECOMMENDATION_CREATED
  → APPROVAL_REQUESTED → APPROVAL_DECIDED (HUMAN:…) → ACTION_EXECUTED → VERIFICATION_COMPLETED
```

Verified: every required event present · ids monotonic with the lifecycle (no impossible
ordering) · the human decision attributed to a person, not the system · references resolve
to real tables · no chain-of-thought markers in any payload.

**A defect was found and fixed here.** The approval-request endpoint originally re-ran the
whole decision pipeline to obtain a policy object for the approval payload, emitting 13
duplicate `SIMULATION_COMPLETED` events and — more seriously — risking minting a
recommendation different from the one the operator was looking at. The audit chain went from
32 events to 19.

---

## Demo Reset

`POST /api/demo/reset` truncates an **explicit allow-list** of eight workflow tables with
`RESTART IDENTITY`, so a re-run produces recommendation 1 rather than recommendation 47.

It cannot touch observed data. `TRUNCATE … CASCADE` is deliberately not used — cascading is
exactly how a reset reaches a table nobody intended. Three tests defend this: workflow and
protected table lists must not intersect; the truncate statement must not name any protected
table; and the module must not contain `CASCADE`.

---

## Security

- The browser reaches **only** the Aventum API. No SQL, no query endpoint, no connection
  string, no credential, no filesystem or shell access, no Ollama administrative control.
  Asserted by a test that scans every read endpoint's real payload for connection strings,
  SQL keywords and the dev password.
- **Every state-changing request is validated server-side.** A forged frontend gains
  nothing: it can ask, and the backend decides.
- CORS is a **closed origin list**, not `*`.
- The global exception handler returns a stable code and one sentence. No stack trace, no
  SQL, no database detail — verified live when a genuine column error was hidden from the
  browser and logged server-side.
- Approval requires an attributable identity; a decision without one is refused 400.

---

## Accessibility

Semantic `<button>`/`<table>`/`<ol>` throughout; `scope` on headers and `<caption>` on every
table. The focus ring from the original design is preserved. Status is never colour-only:
health dots differ in **shape**, completed steps carry screen-reader text, and the gateway
chart has an `sr-only` data table alongside it. `aria-expanded` on disclosures, `aria-busy`
on loading regions, `role="alert"` on errors, `aria-current="step"` on the workflow nav.

---

## Performance

| Endpoint | Latency |
|---|---|
| `/api/health` | **3.6 ms** |
| `/api/overview` | ~120 ms |
| `/api/incidents/{id}` | ~180 ms |
| `/api/incidents/{id}/simulations` | ~40 ms |
| `/api/incidents/{id}/recommendation` | ~90 ms |
| `/api/incidents/{id}/audit` | ~25 ms |
| `POST /analyze` (13 simulations) | ~1.4 s |
| Frontend build | 402 ms · 290 KB (**82 KB gzipped**) |

**A real blocking defect was found and fixed.** `/api/health` took **4.06 s** — every call
probed a *down* Ollama, which on this platform is discovered by timeout rather than refusal.
Health is polled by every open tab, so it was the slowest thing in the product and pushed
concurrent callers past their own client timeout, leaving System Health stuck on "CHECKING".
A 15-second probe cache took it to **3.6 ms**. The probe is still real; it is just not
repeated more than once per window.

Polling happens in exactly one place: while an action is executed but not yet verified. It
stops the moment that window closes.

---

## Testing

| Suite | Tests | Result |
|---|---|---|
| `test_day5_verification.py` | 29 | pass |
| `test_day5_api.py` | 28 | pass |
| **Day 5 total** | **57** | **pass** |
| Full backend + agent suite | 602 | see Final Readiness |

No previous test was deleted or weakened.

The verification tests are deliberately weighted toward **negative** outcomes — eight
parametrised classification cases covering no movement, sub-threshold movement, a badly
missed projection, a negative movement, and integrity failure. A verifier that cannot
disagree with the action it grades is a formality.

The API tests are weighted toward **refusals**: execution without approval, approval without
identity, forged decision values (including a SQL-injection-shaped string), duplicate
approval, duplicate decision, duplicate execution, duplicate verification, blocked
recommendation reaching approval, and expired approval. A green result on any of them would
be a security finding.

---

## Data Integrity

Verified after all Day 5 work:

| | Value | Status |
|---|---|---|
| Canonical fingerprint | `12dec963bd8542feb7171c8efb0baeaed6a1ae1652c76bc1d0827ba88eb5f4b8` | **unchanged** |
| Generation fingerprint | `e8414edd5a58c6cf04876e1bf48ca9a5564cf8d77da8eca4201c1732f52fe3c8` | **unchanged** |
| Observed transactions | 250,000 | **unchanged** |
| Alembic head | `0007` | +1 additive table |

Migration 0007 adds `verifications` only. No ALTER, no DROP, and nothing in Days 1–4 is
touched. No expected fingerprint was edited.

---

## Business Value

Projected and actual are reported separately everywhere they appear:

- **Projected GMV retained ₹19,126.26** — a simulation forecast over a recommendation.
- **Actual simulated GMV recovered ₹14,668.00** — an independent measurement over a verified
  action.
- **Recovery uplift 76.7%** — measured ÷ projected, the honest question to ask of a model
  that authorised an intervention.
- Failure-rate improvement 20.83% → 17.42% on the treated cohort; 79 transactions moved.

**No production recovery is claimed anywhere.** Every persisted verification carries the
sentence in its own row, and the UI repeats it: both sides of every comparison are modelled
outcomes over observed transaction amounts under a synthetic incident.

---

## Deployment / Run Instructions

**Prerequisites:** Docker, Python 3.14, Node 20+, and (optional) Ollama with `qwen3:8b`.

```bash
# 1. Database
cd aventum/backend && docker compose up -d

# 2. Backend deps + migrations
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m alembic upgrade head

# 3. API  (http://localhost:8000)
.venv/Scripts/python.exe -m uvicorn aventum_api.app:app --port 8000

# 4. Frontend  (http://localhost:5173)
cd "../razorpay frontend new" && npm install && npm run dev

# 5. Optional: the agent
ollama serve && ollama pull qwen3:8b
```

Environment: `backend/.env.example` and `razorpay frontend new/.env.example` document every
variable. The defaults work with no configuration. `.env*` is gitignored; no secret is
committed.

**Demo reset:** `POST /api/demo/reset`, or the Overview screen. Restores a clean flagship
state without manual SQL.

---

## Known Limitations

- **The agent is optional and was mostly exercised in its unavailable state.** Deterministic
  degradation is thoroughly verified; the agent-driven path through the UI is wired and
  typed but has had less live exercise than the deterministic one.
- **No time-series telemetry exists**, so latency medians and success-over-time render
  `UNAVAILABLE`. The health chart shows a per-gateway comparison instead.
- **Capacity is `UNAVAILABLE` throughout** — no capacity telemetry exists anywhere in the
  system, and inventing one would be the fabricated production figure the project forbids.
- **Verification uses a pre/post comparison on one cohort, not a randomised control.** No
  concurrent untreated arm exists in this data, and the limitation is persisted with every
  verification row.
- **One measurement window.** No durability claim is made about whether an improvement
  persists.
- **The overview ranks by significance**, so a systemic 24.52σ incident outranks the
  flagship gateway_C incident at 9.26σ. That ordering is deterministic and defensible, but
  it means the flagship is not the top row.
- **No authentication.** The approver identity is typed by the operator, not authenticated.
  This is a prototype boundary, and a production deployment would need real identity before
  the approval trail could be relied on.
- **Single-user assumptions.** Concurrency is safe at the database level (unique constraints
  on approval, action and verification identity), but there is no live multi-user
  presence or conflict UI.
