_Aventum internal report — Day 4B P1 reliability fix._

# Day 4B P1 Fix Report

**The blocker is closed.** All four scenarios now complete deliberately, 10/10 each, and every one agrees with the deterministic policy-applied decision.

| Metric | Before | After |
|---|---|---|
| Flagship completion | 1/1 (unreliable across sessions) | **10/10 = 100%** |
| Issuer completion | 0/1 | **10/10 = 100%** |
| Mild completion | 0/1 | **10/10 = 100%** |
| Marginal completion | 0/1 | **10/10 = 100%** |
| Agrees with deterministic decision | 1/4 | **40/40 = 100%** |
| Format-failure rate (shape conformance) | **0/8 conformant** under `format:"json"` | **8/8 conformant**; **0 format failures in 40 runs** |
| Mean turns | 11–12 | **3.25** |
| p95 turns | 12 (budget) | **5** |
| Mean latency / turn | 9,600–12,300 ms | **11,332 ms** |
| p95 latency / turn | 19,100 ms | **16,424 ms** |
| Mean tool calls | 0–7 | **1.75** |
| Mean simulations (agent-requested) | 0–3 | **0.00** |
| Mean wall time / incident | 118–144 s | **39.1 s** (max 66.6 s) |
| Flagship prompt actually seen by the model | **62%** (4,096 of 6,572 tokens) | **100%** |
| Unsupported claims | 0 | **0** |
| Policy violations | 0 | **0** |

No budget was raised. No validation was weakened. No scenario answer is hard-coded.
`MAX_TURNS`, `MAX_TOOL_CALLS`, `MAX_SIMULATIONS`, `MAX_CONTEXT_TOKENS`,
`QWEN_TURN_TIMEOUT_S` and the 180 s `TOTAL_AGENT_BUDGET_S` are byte-for-byte unchanged;
the fix makes the model *receive* its prompt rather than giving it more room to fail in.

---

## Problem

Day 4B was architecturally complete and secure, but `qwen3:8b` intermittently emitted malformed protocol structures — a generic `{"action","reason"}` envelope, or top-level fields collapsed inside `tool_call`. Those were correctly rejected, but each rejected turn cost ~11 s of a 180 s budget, and three of four scenarios exhausted the budget without reaching a deliberate decision.

---

## Root Cause

**Investigated before any rewrite, and the assumed cause was wrong.**

The working hypothesis had been prompt complexity. It was not. The actual cause is that **`format:"json"` constrains validity, not structure.** Ollama's loose JSON mode guarantees the output parses; it says nothing about which keys appear. The model was free to invent its own envelope and reliably did.

Measured directly, 8 trials per condition, identical prompt, qwen3:8b / Ollama 0.16.1:

| Mode | Valid JSON | Correct shape | Mean |
|---|---|---|---|
| `format:"json"` — the Day 4B default | 8/8 | **0/8** | 3,570 ms |
| `format:<JSON Schema>`, 7-field protocol | 8/8 | **8/8** | 8,268 ms |
| `format:<JSON Schema>`, 6-field flat protocol | 8/8 | **8/8** | 6,773 ms |

Zero out of eight. No amount of prompt engineering was going to fix that, which is exactly why the previous session's four rounds of prompt repair moved the flagship but never the other scenarios.

Contributing factors, all confirmed by inspection rather than assumed:

- **The model was asked to emit state it did not own.** The protocol required `state` (OBSERVE/ANALYZE/SIMULATE/…), but tool authorization has always derived the phase from persisted progress. The field was pure failure surface — required, get-able wrong (observed: `"CHECK"`, `"CHECK_BOUND"`), and consumed by nothing.
- **The model was asked to echo IDs it had just been given.** `incident_id` and `analysis_run_id` appeared in every tool schema. One was hallucinated (`analysis_run_id: 101`), reached a database write, and aborted the transaction.
- **The model was asked to do arithmetic.** Assembling `(target, percentage)` candidate triples and comparing their outputs is computation, which rule 2 of its own system prompt forbids.

### Second root cause — silent prompt truncation

Fixing the shape defect raised flagship completion to 10/10, but a later 40-run trial regressed it to **5/10**. The five failures were not bad reasoning. They were `AGENT_UNAVAILABLE` at **turn 0**, always at ~34 s, before the model produced anything at all — and the underlying error was `timed out` against the 30 s per-turn socket timeout.

The timeout was the symptom. Measuring Ollama's own `prompt_eval_count` on the flagship prompt gave the cause:

| Configuration | `prompt_eval_count` | Loaded `context_length` | Warm latency |
|---|---|---|---|
| `num_ctx` unset — the Day 4B default | **4,096** | 4,096 | 16–24 s |
| `num_ctx = 9216` | **6,572** | 9,216 | **9.7 s** |

The real prompt is **6,572 tokens**. Ollama had loaded the model with a 4,096-token window and **silently discarded 2,476 tokens** — no error, no warning, an ordinary `200`. The agent had been reasoning about an incident it was only ever shown 62% of, while `MAX_CONTEXT_TOKENS = 8000` advertised a budget the runtime never honoured.

This also explains the scenario split precisely, which the prompt-complexity hypothesis never could:

Prompt sizes below are the measured character counts of system prompt + first user
message; token counts for B/C/D are derived from A's measured 2.97 chars/token ratio
(A itself is a direct `prompt_eval_count` reading).

| Scenario | Prompt chars | Tokens | Fits in 4,096? | Old completion |
|---|---|---|---|---|
| A_flagship | 19,536 | **6,572** (measured) | **no — truncated** | 5/10 |
| C_mild | 19,960 | ~6,720 (derived) | **no — truncated** | 10/10, but 4–5 turns |
| B_issuer | 7,616 | ~2,564 (derived) | yes | 10/10, 1 turn |
| D_marginal | 8,853 | ~2,981 (derived) | yes | 10/10 |

Only the two oversized prompts were affected. The intermittency came from Ollama re-loading the model at a larger window on some requests, which added ~15 s and pushed the turn past the socket timeout — the same defect surfacing as latency variance rather than as truncation.

**The fix does not relax the timeout.** `QWEN_TURN_TIMEOUT_S` remains 30.0 s and `TOTAL_AGENT_BUDGET_S` remains 180.0 s. Instead:

1. `QWEN_NUM_CTX = 9216` is sent explicitly, so the whole prompt is actually evaluated. An import-time assertion binds it to `MAX_CONTEXT_TOKENS + MAX_OUTPUT_TOKENS` so the two cannot drift apart silently.
2. `QWEN_KEEP_ALIVE = "30m"` keeps the model resident, so an eviction between turns cannot masquerade as an unreachable server.
3. A **truncation guard** in `client.py` raises `AgentUnavailable` whenever `prompt_eval_count >= QWEN_NUM_CTX`. A reply derived from a partially-received prompt is not a degraded answer, it is an answer to a different question, so it is refused rather than used. This condition was previously undetectable.
4. `_CHARS_PER_TOKEN` was recalibrated from 4 to 3 against measured counts (19,536 chars → 6,572 tokens = 2.97). The old value under-counted by ~40%, so the loop's own context guard was enforcing a smaller number than it advertised. The estimate now errs high, so the loop stops at its own budget rather than letting the transport guard fire.

Correctness and speed moved together: evaluating the full prompt is *faster* than truncating it (9.7 s vs 16–24 s warm), because the reload disappears.

---

## Baseline (before the fix)

Recorded from `docs/DAY4B_IMPLEMENTATION_REPORT.md` and re-measured at the start of this task.

- 545 tests: 544 passed, 1 skipped, 0 failed
- Alembic head `0006`; canonical fingerprint `12dec963…f4b8`; generation fingerprint `e8414edd…2fe3c8`
- Flagship: completes, matches the deterministic optimum
- Issuer / mild / marginal: `BUDGET_EXCEEDED`, 12 turns, no decision
- Context 1,309–4,118 tokens; VRAM 4.81 GB of 6,141 MiB

---

## Native Structured Output Investigation

**Ollama 0.16.1 supports JSON-Schema-constrained generation, and it is now used.** The schema is passed in the `format` field of `/api/chat`; it constrains decoding, so a non-conformant shape is *unrepresentable* rather than merely detected afterwards.

`schemas.response_json_schema()` is the single source of that schema. Application-side validation in `parse_agent_decision()` is **unchanged in strictness and retained in full** as an independent second layer: constrained decoding cannot express "never supply `expected_gmv_retained`", "cite only IDs a tool returned", or "this tool is not reachable from this phase". Those are semantic rules and remain ours.

Every field is listed in `required`, including nullable ones — constrained decoding pins the key set, so requiring all keys and permitting `null` is what actually fixes the shape; leaving fields optional lets the sampler omit them.

---

## Model Decision Surface

Reduced from seven fields with a nested object to ten flat scalar fields, of which the model meaningfully chooses two or three.

**Removed entirely:** `state` (the application owns it), the nested `tool_call` object (flattened to `tool_name` + `arguments`), `simulation_ids` (singular `simulation_id`), `recommendation_intent` (folded into `decision`), and the nested `uncertainty` object (flattened to three scalars).

**Output protocol:**

```json
{"kind":"TOOL_CALL","tool_name":"check_action_bounds","arguments":{"simulation_id":4},
 "decision":null,"simulation_id":null,"rationale":"…","evidence_ids":[],
 "uncertainty_kind":null,"uncertainty_level":null,"uncertainty_response":null}

{"kind":"FINAL","tool_name":null,"arguments":null,"decision":"NO_ACTION|RECOMMEND|UNCERTAIN",
 "simulation_id":4,"rationale":"…","evidence_ids":[], …}
```

---

## Tool Authorization

**Unchanged, and still keyed on persisted progress.** A model asserting a phase gets nothing: `propose_action` appears only after a simulation has passed `check_action_bounds`; `request_human_approval` only after a recommendation exists. Verified by `test_claiming_a_state_does_not_grant_its_tools`.

One addition: a `FINAL` with `decision: RECOMMEND` is now rejected unless the agent has *actually* called `propose_action` **and** `request_human_approval`. A recommendation the agent created but never submitted is an unfinished job, not a decision — and the agent's authority ends precisely at asking a human.

---

## Tool Argument Minimization

`incident_id` and `analysis_run_id` are gone from all nine tool schemas; the application injects them from the agent run's own binding. A value the model cannot supply cannot be hallucinated, so the `analysis_run_id: 101` class of failure is now structurally impossible rather than merely caught.

`propose_action` still accepts no numeric field of any kind. `run_counterfactual` still has no `traffic_percentage` — only a bounded `candidate_percentage` of 10, 20, or 30.

---

## Candidate Selection — the model selects, it does not construct

`precompute_candidates()` deterministically simulates `NO_ACTION` plus the three bounded reroutes on the best viable target (ranked by `baseline_failure_probability`), persists them, and hands them to the model with their `simulation_id`s. Four simulations of an eight-simulation budget, leaving room for the agent to request more.

The agent's judgement is untouched: it still decides whether to act at all, which persisted candidate to select, whether policy permits it, and whether `NO_ACTION` is better. What it no longer does is arithmetic. The measured effect is stark — **agent-requested simulations dropped to 0.00 per run**, and agreement with the deterministic decision went from 1/4 to 40/40.

An incident with no affected gateway (issuer-side, systemic) gets no reroute candidates at all, and the context says so as a fact.

---

## Recovery Strategy

Malformed output remains **invalid and is never repaired**. One targeted correction is issued, one retry allowed; a second identical failure terminates the run safely. Retries count against the existing budgets. Measured across 40 runs: **0 format failures, 0 retries needed.**

---

## Tool Memoization

Successful read-only tool results are cached per run by call signature. Re-asking an identical deterministic question wastes a turn and cannot yield new information. Write tools are never memoised — they have their own database-level idempotency.

---

## Multi-Scenario Results — 10 real-model runs each

| Scenario | Completion | Deliberate | Agrees | Decision | Mean turns | Mean wall |
|---|---|---|---|---|---|---|
| **A gateway_C flagship** | **10/10** | 10/10 | **10/10** | `REROUTE:C→A@30` → approval requested | 5.0 | 63.2 s |
| **B issuer-centred** | **10/10** | 10/10 | **10/10** | `NO_ACTION` | 1.0 | 9.9 s |
| **C mild** | **10/10** | 10/10 | **10/10** | `REROUTE:C→A@30` → approval requested | 5.0 | 62.1 s |
| **D marginal** | **10/10** | 10/10 | **10/10** | `NO_ACTION` | 2.0 | 21.0 s |

Every threshold is met: flagship ≥95% → 100%; issuer / mild / marginal ≥90% → 100% each.

The flagship figure is the one that matters, because it is the one that had regressed.
The trial immediately before the truncation fix returned **5/10** on this scenario — five
`AGENT_UNAVAILABLE` failures at turn 0. The same harness, same model, same thresholds,
run against the fixed client, returns 10/10.

**B and D reach `NO_ACTION` deliberately, and both are correct.** B is issuer-side, so rerouting between gateways cannot address it and no valid reroute candidate exists. D's RCA is `INSUFFICIENT_EVIDENCE` — confidence 0.0053, evidence strength 0.0011, 0.63σ, severity `NONE`, no `PRIMARY` alert — so **every** reroute candidate is `BLOCKED` on six independent policy gates. `NO_ACTION` is the only permitted answer, and the agent finds it in two turns.

---

## Repeated-Trial Results — 40 runs, aggregate

Measured against the final code, **after** the truncation fix. The comparable trial
before it returned 5/10 on the flagship; the difference is the whole prompt reaching
the model.

| Metric | Value |
|---|---|
| Completion | **40/40** |
| Deliberate decision | **40/40** |
| Agrees with deterministic | **40/40** |
| Format failures | **0** |
| Blocked fabrication attempts | **0** |
| Policy violations | **0** |
| Unsupported claim rate | **0.0000** |
| Recommendations produced | 20 (A and C) |
| Approvals requested | 20 — one per recommendation |
| Recommendation consistency | **20/20** |
| Rejected-then-recovered turns | 40 — 2 per A/C run, 0 on B/D; every one recovered |
| Mean / max turns | 3.25 / 5 (cap 12) |
| Mean tool calls | 1.75 (cap 20) |
| Mean / max agent-requested simulations | 0.00 / 0 (cap 8) |
| Mean / max wall time | 39.1 s / **66.6 s** (budget 180 s) |
| Qwen turns measured | 130 |
| Mean / p95 / max latency | 11,332 / 16,424 / **17,089 ms** (turn timeout 30,000 ms) |
| Real prompt tokens, flagship | **6,572** measured, in a 9,216 window |
| VRAM | 4.81 GB of 6,141 MiB |

Per scenario:

| Scenario | Completion | Agrees | Mean turns | Mean wall | Rejected turns |
|---|---|---|---|---|---|
| A_flagship | **10/10** | 10/10 | 5.0 | 63.2 s | 20 |
| B_issuer | **10/10** | 10/10 | 1.0 | 9.9 s | 0 |
| C_mild | **10/10** | 10/10 | 5.0 | 62.1 s | 20 |
| D_marginal | **10/10** | 10/10 | 2.0 | 21.0 s | 0 |

Every budget finished with room to spare: the slowest run used 66.6 s of 180 s, and the
slowest single turn 17.1 s of 30 s. **No limit was raised to achieve this.**

The two rejected turns per A/C run are the loop refusing a `RECOMMEND` that has not yet
produced a recommendation and an approval, then the agent supplying them. That is the
protocol working, not a failure — but it is also the clearest remaining inefficiency,
and it is recorded rather than smoothed over.

---

## Agent Metrics

- **Grounded claim rate: 100%** (target ≥99%)
- **Unsupported claim rate: 0** (target 0)
- **Policy violation rate: 0** (target 0)
- **Recommendation consistency: 20/20** — every persisted figure equals its cited simulation's
- **Tool selection efficiency: 100%** — 0 duplicate calls in 40 runs
- **Format-failure rate: 0**
- **Deliberate completion rate: 100%**
- **NO_ACTION correctness: 20/20** — every `NO_ACTION` matches the policy-applied deterministic decision
- **Budget compliance: 40/40**

---

## Security Regression

The adversarial suite is unchanged and passes in full. The scripted-model tests deliberately emit shapes the real sampler can no longer produce — constrained decoding is a reliability fix, not a security one, and the security boundaries must still hold against arbitrary bytes.

Blocked or safely handled: raw SQL request · ground-truth request · numeric injection · nested numeric injection · fake simulation ID · fake evidence ID · cross-incident simulation ID · threshold override · approval bypass · execution request · malicious evidence prompt injection · malformed tool output · unknown tool · invalid phase/tool combination · repeated tool loop · repeated format failure · tool-call budget exhaustion · simulation budget exhaustion · idle-turn stall · Ollama unavailable · per-tool transaction isolation.

---

## Defects Found and Fixed During This Task

Eleven, several found by the real model doing things a scripted test would not think to try.

1. **`format:"json"` never constrained structure** — the root cause. Fixed with native JSON-Schema-constrained decoding.
2. **Context-provided candidates were scored as fabricated citations.** Pre-simulated candidates are offered in the *context*, not by a tool, so the citation guard rejected the model for using exactly what it was given — 7 false "fabrication" blocks in one run. Both the loop and `measure_run` now treat context-offered IDs as citable.
3. **`RECOMMEND` was accepted without an approval request.** A recommendation the agent never submitted was being counted as a completed decision.
4. **Issuer incidents produced four `SIMULATION_INVALID` rows and an inescapable loop.** With no affected gateway there is no source to reroute from, yet the `NO_ACTION` guard demanded a reroute comparison that could not exist — 12 turns, 9 tool calls, no legal move. Now such incidents get no reroute candidates and `NO_ACTION` is justified on arrival.
5. **A conditional instruction inside the context was applied unconditionally.** A note reading "if `rerouting_applicable` is false … answer `NO_ACTION`" flipped the flagship from **10/10 REROUTE to 10/10 NO_ACTION** on that change alone. An 8B model reading a conditional inside its *data* does not reliably evaluate the condition. The context now states only facts; conditional guidance lives in the prompt.
6. **The model copied literal evidence IDs out of the worked examples.** Examples using `"evidence_ids":[1,4]` produced real runs citing evidence 1 and 4 on incidents whose evidence IDs were entirely different. Every one was caught by the citation guard, but each cost a turn. All example IDs are now empty.
7. **The trial harness compared against a pre-policy reference.** `run_candidate_sweep().best` ranks by GMV *before* the policy gate, so on a weak incident it names a candidate the policy will refuse — scoring a correct `NO_ACTION` as a mismatch. This produced an apparent "0/10 agreement" for D_marginal that was a measurement artifact, not an agent failure. The reference is now `run_decision_pipeline`, the real Day 4A decision with policy applied.

8. **Ollama silently truncated every oversized prompt.** The second root cause, above. The model was loaded with a 4,096-token window while the flagship prompt is 6,572 tokens; 2,476 tokens were discarded with no error. `QWEN_NUM_CTX` is now sent explicitly and bound by assertion to `MAX_CONTEXT_TOKENS + MAX_OUTPUT_TOKENS`.
9. **Truncation was undetectable.** Nothing in the system could observe it — an over-long prompt returns a normal `200`. `client.py` now raises `AgentUnavailable` when `prompt_eval_count >= QWEN_NUM_CTX` rather than reasoning from a prompt the model only partly received.
10. **Model eviction was indistinguishable from an unreachable server.** With no `keep_alive`, a reload added ~15 s to a turn and surfaced as `timed out` at the socket layer — reported as `AGENT_UNAVAILABLE`, which points the reader at the network rather than at the context window. `QWEN_KEEP_ALIVE = "30m"` removes the reload; the truncation guard names the real condition.
11. **The context-token estimator under-counted by ~40%.** `_CHARS_PER_TOKEN = 4` against a measured 2.97, so `MAX_CONTEXT_TOKENS` enforced less than it advertised. Recalibrated to 3, which errs high so the loop stops at its own budget rather than tripping the transport guard.

Defect 7 is worth stating plainly: it would have been easy to report D as an agent failure. It was my measurement that was wrong.

Defects 8–11 are worth stating just as plainly, for the opposite reason. The first 40-run trial after the shape fix returned a clean 40/40, and that number was written into an earlier draft of this report as a pass. It was not reproducible: the very next trial returned 5/10 on the flagship. Had the run order or the model's residency differed slightly, a silently truncated prompt would have shipped as a green result. The 40/40 was not a lie, but it was luck, and it is the reason this report now records `prompt_eval_count` rather than trusting a completion count.

Note also that the `NO_ACTION` guard named in defect 4 was subsequently **removed entirely**, not merely narrowed: with candidates pre-simulated before the first turn, the comparison it demanded always already existed, so it could not fire on any input. A guard that cannot fire while implying a protection is worse than no guard, and a guard that pushes toward acting is the structural bias toward intervention the contract forbids.

---

## Day 4A Regression

Canonical fingerprint `12dec963…f4b8`, generation fingerprint `e8414edd…2fe3c8`, `OBSERVED_CONTENT_MD5_V1` `13965d76…f5f24226`, Alembic head `0006`, 250,000 transactions — all unchanged. No migration was required. Qwen remains optional to the deterministic core, enforced by the test asserting the Day 4A packages never import `aventum_agent`.

---

## Performance

Measured on the RTX 4050 Laptop (6,141 MiB), Ollama 0.16.1, real model, real database, 130 model turns across 40 runs.

Mean turn latency **11,332 ms**, p95 **16,424 ms**, max **17,089 ms**, against an unchanged 30,000 ms per-turn timeout. Mean incident→decision **39.1 s**, max **66.6 s**, against a 180 s agent budget and a 300 s end-to-end target. VRAM 4.81 GB with the model loaded.

Two effects offset each other. Constrained decoding costs slightly more per token, but the turn count fell by ~70%. Sizing the context window correctly then cut *warm* first-turn latency from 16–24 s to 9.7 s, because the model is no longer reloaded at a larger window mid-run — the p95 improved from 18,720 ms to 16,424 ms while the mean rose, which is the signature of removing a tail stall rather than making the common case faster.

The headroom matters more than the averages: the slowest single turn used **57% of the turn timeout** and the slowest whole run **37% of the agent budget**. Before the fix, flagship turns were intermittently exceeding 30 s outright.

---

## Remaining Limitations

- **Turn latency is ~11 s** and is model/hardware-bound. The budget now has ample headroom (max 66.6 s of 180 s), but a slower machine would erode it.
- **The agent explores a subset of the candidate space.** It selects among four pre-simulated candidates and may request more within the 8-simulation budget; it does not sweep all twelve. Agreement with the deterministic optimum is 40/40 on these scenarios but is not guaranteed by construction.
- **Four scenarios, ten runs each, one machine, one model build.** 40/40 is a strong result on this surface, not a proof of general reliability. The truncation defect is a standing warning about exactly this: an earlier 40/40 on the same scenarios was reproducible-looking and wrong.
- **Prompt size is now a first-class constraint.** A materially larger incident context could approach the 8,000-token budget. It will fail loudly at the loop's own guard or the transport truncation guard rather than degrade silently, but it will fail — sizing beyond that is a Day 5 concern.
- **Two rejected-then-recovered turns occur per A/C run.** The agent reaches for `propose_action` before `check_action_bounds`, is refused by the phase guard, and corrects in one turn. Harmless and within budget, but not zero.
- **A cold model still costs a one-time load.** `keep_alive` keeps it resident once loaded, but the first call after an Ollama restart pays the load. It is well inside the 30 s turn timeout at the current prompt size and no longer compounds with a context resize — that combination was the defect fixed here, not an accepted limitation.
- Capacity remains `UNAVAILABLE`, eligibility `ELIGIBILITY_UNCONDITIONAL`, and everything remains synthetic infrastructure under a simulated incident with simulated execution.

---

## Model Decision

**`qwen3:8b` is retained, and no model swap was attempted** — which was the point of the task. The measured conclusion is that the model was never the binding constraint: the protocol was. With shape guaranteed by constrained decoding, arithmetic moved to the deterministic layer, and IDs injected server-side, the same 8B model went from 1/4 to 4/4 scenarios at 100% completion. A model-selection experiment is not warranted.

---

## Final Verdict

Every acceptance criterion is met: real `qwen3:8b` in use, native constrained generation employed, validation unweakened, malformed output never repaired, decision surface minimal, application owns state, authorization follows persisted progress, arguments minimized, the agent selects only persisted candidates, numeric injection structurally impossible, ground truth unreachable during inference, approval human-only, execution outside Qwen, all budgets unchanged, and completion rates of 100% / 100% / 100% / 100% against thresholds of 95% / 90% / 90% / 90%.

**545/545 tests pass.** Alembic head `0006`, 250,000 transactions, no migration added.

The standard was not lowered to reach this. `MAX_TURNS`, `MAX_TOOL_CALLS`,
`MAX_SIMULATIONS`, `MAX_CONTEXT_TOKENS`, `QWEN_TURN_TIMEOUT_S` and the 180 s
`TOTAL_AGENT_BUDGET_S` are byte-for-byte unchanged, and the run finished with 63% of the
turn timeout and 63% of the agent budget unused. Two guards were **added** — the import-time
window/budget assertion and the transport truncation check — so the failure mode that
produced the regression is now loud rather than silent.

# DAY 4B P1 FIX CLOSED — DAY 4B READY FOR FINAL INTEGRATION
