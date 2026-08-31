# Day 4B P1 Fix Handoff — Reliable Multi-Scenario Completion

Snapshot of the files changed by the **Day 4B P1 reliability fix**. Paths mirror the real repo.

All 11 files verified byte-identical to the live repo at copy time.

---

## Status

# DAY 4B P1 FIX CLOSED — DAY 4B READY FOR FINAL INTEGRATION

**545/545 tests pass.** Alembic head `0006`, 250,000 transactions, no migration added. All four live scenarios complete **10/10** against the real `qwen3:8b`.

---

## What changed, in one table

| | Before | After |
|---|---|---|
| Flagship completion | 5/10 (and 1/1 unreliable before that) | **10/10** |
| Issuer / mild / marginal completion | 0/1 each | **10/10 each** |
| Agrees with deterministic decision | 1/4 | **40/40** |
| Shape conformance | **0/8** under `format:"json"` | **8/8**; 0 format failures in 40 runs |
| Flagship prompt actually seen by model | **62%** (4,096 of 6,572 tokens) | **100%** |
| Mean turns | 11–12 | **3.25** |
| Mean wall / incident | 118–144 s | **39.1 s** (max 66.6 s of 180 s) |
| Max single-turn latency | intermittently >30 s → `AGENT_UNAVAILABLE` | **17.1 s** of 30 s |
| Tests | 545 | **545** |

**No budget was raised, no validation weakened, no scenario answer hard-coded.**
`MAX_TURNS=12`, `MAX_TOOL_CALLS=20`, `MAX_SIMULATIONS=8`, `MAX_CONTEXT_TOKENS=8000`,
`QWEN_TURN_TIMEOUT_S=30.0`, `TOTAL_AGENT_BUDGET_S=180.0` — all byte-for-byte unchanged.

---

## Root cause 1 — `format:"json"` never constrained structure

Ollama's loose JSON mode guarantees the output *parses*; it says nothing about which keys
appear. Measured, 8 trials per condition, identical prompt:

| Mode | Valid JSON | Correct shape |
|---|---|---|
| `format:"json"` — the Day 4B default | 8/8 | **0/8** |
| `format:<JSON Schema>` | 8/8 | **8/8** |

Zero out of eight. The prior session's four rounds of prompt repair could never have fixed
this, which is why they didn't. Fixed with native JSON-Schema-constrained decoding, so a
non-conformant shape is *unrepresentable* rather than merely rejected afterwards.

Application-side validation in `schemas.py` is **unchanged in strictness** and retained as
an independent second layer. Constrained decoding cannot express "never supply
`expected_gmv_retained`", "cite only IDs a tool returned", or "this tool is not reachable
from this phase". Those are semantic rules and remain ours.

---

## Root cause 2 — Ollama silently truncated the prompt

Fixing the shape raised the flagship to 10/10, then a later trial regressed it to **5/10**.
The failures were not bad reasoning: `AGENT_UNAVAILABLE` at **turn 0**, ~34 s, before the
model emitted anything.

| Configuration | `prompt_eval_count` | Loaded window | Warm latency |
|---|---|---|---|
| `num_ctx` unset — the Day 4B default | **4,096** | 4,096 | 16–24 s |
| `num_ctx = 9216` | **6,572** | 9,216 | **9.7 s** |

The real prompt is 6,572 tokens. Ollama had loaded a 4,096-token window and **silently
discarded 2,476 tokens** — no error, an ordinary `200`. The agent was reasoning about an
incident it had been shown 62% of, while `MAX_CONTEXT_TOKENS = 8000` advertised a budget
the runtime never honoured. Intermittency came from Ollama re-loading at a larger window on
some requests, adding ~15 s and pushing the turn past the 30 s socket timeout.

It explains the scenario split exactly, which the prompt-complexity hypothesis never could:
A (6,572 tok) and C (~6,720) overflowed and were unreliable; B (~2,564) and D (~2,981) fit
and always passed.

**The fix does not relax the timeout.** Instead:

1. `QWEN_NUM_CTX = 9216` sent explicitly, bound by an **import-time assertion** to
   `MAX_CONTEXT_TOKENS + MAX_OUTPUT_TOKENS` so the two cannot drift apart silently.
2. `QWEN_KEEP_ALIVE = "30m"` — an evicted model reloading mid-run is no longer
   indistinguishable from an unreachable server.
3. **Truncation guard**: `client.py` raises `AgentUnavailable` when
   `prompt_eval_count >= QWEN_NUM_CTX`. A reply derived from a partially-received prompt is
   not a degraded answer, it is an answer to a different question. This condition was
   previously undetectable.
4. `_CHARS_PER_TOKEN` recalibrated 4 → 3 against measured counts (19,536 chars → 6,572
   tokens = 2.97). The old value under-counted by ~40%, so the loop's own guard enforced
   less than it advertised.

Correctness and speed moved together: evaluating the full prompt is *faster* than
truncating it, because the reload disappears.

---

## Why the earlier 40/40 was luck

The first 40-run trial after the shape fix returned a clean 40/40 and was written into a
draft of the report as a pass. It was not reproducible — the very next trial returned 5/10
on the flagship. Had run order or model residency differed slightly, a silently truncated
prompt would have shipped as a green result.

That is why this report records `prompt_eval_count` rather than trusting a completion
count, and why the regression is documented rather than quietly overwritten.

---

## Structural changes to the model's job

- **Removed state the model did not own.** The protocol required `state`
  (OBSERVE/ANALYZE/…), but tool authorization has always derived the phase from *persisted
  progress*. The field was pure failure surface — required, gettable wrong (observed:
  `"CHECK"`), consumed by nothing.
- **Removed IDs the model was asked to echo.** `incident_id` / `analysis_run_id` were in
  every tool schema; one was hallucinated (`analysis_run_id: 101`), reached a database
  write, and aborted the transaction. They are now server-injected.
- **Removed arithmetic.** Candidates are pre-simulated; the model *selects* among
  persisted simulation IDs rather than constructing `(target, percentage)` triples.
  Agent-requested simulations fell to **0.00 per run**.
- **Removed the `NO_ACTION` guard entirely.** With candidates pre-simulated before turn 1,
  the comparison it demanded always already existed, so it could not fire on any input. A
  guard that cannot fire while implying a protection is worse than none — and one that
  pushes toward acting is the structural bias toward intervention the contract forbids.
  Choosing `NO_ACTION` badly is a quality question, measured by agreement with the
  deterministic decision; doing nothing is always safe.

---

## Files

| File | Change |
|---|---|
| `aventum_agent/client.py` | JSON-Schema `format`; `num_ctx`; `keep_alive`; truncation guard |
| `aventum_agent/constants.py` | `QWEN_NUM_CTX`, `QWEN_KEEP_ALIVE`, window/budget assertion |
| `aventum_agent/context.py` | candidates + `valid_simulation_ids` exposed; estimator recalibrated |
| `aventum_agent/evaluation.py` | context-offered IDs citable; budget bound = budget + one turn |
| `aventum_agent/loop.py` | progress-based tool authorization; memoization; `NO_ACTION` guard removed |
| `aventum_agent/prompts.py` | minimal protocol; conditional guidance moved out of context data; example IDs emptied |
| `aventum_agent/schemas.py` | flat protocol; all fields `required`; `incident_id`/`analysis_run_id` dropped from tool schemas |
| `aventum_agent/service.py` | `precompute_candidates`; NO_ACTION-only when no affected gateway |
| `aventum_agent/tools.py` | server-injected IDs; SAVEPOINT per dispatch; reroute-into-degraded refused |
| `tests/test_agent_layer.py` | agent-layer suite updated |

---

## Security regression

Unchanged and passing in full. The scripted-model tests deliberately emit shapes the real
sampler can no longer produce — constrained decoding is a **reliability** fix, not a
security one, and the boundaries must still hold against arbitrary bytes.

Blocked or safely handled: raw SQL · ground-truth request · numeric injection · nested
numeric injection · fake simulation ID · fake evidence ID · cross-incident simulation ID ·
threshold override · approval bypass · execution request · evidence prompt injection ·
malformed tool output · unknown tool · invalid phase/tool combination · repeated tool loop ·
repeated format failure · tool-call budget exhaustion · simulation budget exhaustion ·
idle-turn stall · Ollama unavailable · per-tool transaction isolation.

---

## Day 4A regression

Canonical fingerprint, generation fingerprint, `OBSERVED_CONTENT_MD5_V1`, Alembic head
`0006` and 250,000 transactions all unchanged. No migration required. Qwen remains optional
to the deterministic core, enforced by the test asserting the Day 4A packages never import
`aventum_agent`.

---

## Not touched

No Day 5 work. No frontend. No model switch. No Day 4A redesign. Four scenarios on one
machine and one model build is a strong result on this surface, not a proof of general
reliability — see *Remaining Limitations* in the report.
