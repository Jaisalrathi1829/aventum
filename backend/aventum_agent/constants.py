"""
Agent budgets, states, and vocabulary. System-owned.

Every limit here is a module constant. None is a function parameter, a prompt field, or
anything the model can influence — the model has no tool that reads or writes a budget,
so the only way to change one is to edit this file.
"""

from __future__ import annotations

# ------------------------------------------------------------------ Qwen runtime
# LOCKED. Do not change without fresh measurement on the target hardware.
#
# `think: false` is retained for two reasons, one measured and one structural:
#   * measured (2026-08-28, Ollama 0.16.1, RTX 4050): warm turns average ~4.0 s with
#     thinking disabled vs ~4.2-4.5 s enabled, so disabling is never slower;
#   * structural: no chain-of-thought is produced, so "never store chain-of-thought"
#     is satisfied by construction rather than by a redaction policy that could lapse.
# See docs/DAY4B_IMPLEMENTATION_REPORT.md for the full measurement, including the fact
# that the pre-flight's catastrophic empty-response result did NOT reproduce on this
# Ollama build — the option is kept on merit, not on an unverified historical claim.
QWEN_MODEL = "qwen3:8b"
QWEN_OPTIONS = {
    "model": QWEN_MODEL,
    "think": False,
    "temperature": 0,
    "format": "json",
}
OLLAMA_BASE_URL = "http://localhost:11434"

# Per-turn output cap. Large enough for a schema-valid decision with a short summary,
# small enough that a runaway generation cannot consume the whole turn budget.
MAX_OUTPUT_TOKENS = 512

# ------------------------------------------------------------------ budgets
MAX_TURNS = 12
MAX_TOOL_CALLS = 20
MAX_SIMULATIONS = 8
MAX_CONTEXT_TOKENS = 8_000

# The context window the model is LOADED with.
#
# This is not a tuning knob, it is a correctness requirement. Ollama sizes a model's
# context per request and SILENTLY TRUNCATES any prompt that does not fit -- it does
# not error. Measured on the flagship incident before this was set: the real prompt is
# 6572 tokens, `prompt_eval_count` came back as exactly 4096, and 2476 tokens were
# discarded without a word. The agent was reasoning about an incident it had only
# partially been shown, while `MAX_CONTEXT_TOKENS` below advertised a budget of 8000
# that the runtime never honoured.
#
# It must therefore cover the largest prompt we permit PLUS the output generated into
# the same window, or the declared budget is fiction. The assertion keeps the two from
# drifting apart if either budget is ever changed.
QWEN_NUM_CTX = 9_216

# How long Ollama keeps the model resident. A model evicted between turns is reloaded
# on the next call, adding ~15s that is indistinguishable from a hung server at the
# socket timeout. Keeping it resident removes that variance at no cost to correctness.
QWEN_KEEP_ALIVE = "30m"

QWEN_TURN_TIMEOUT_S = 30.0
TOOL_TIMEOUT_S = 10.0
COUNTERFACTUAL_TIMEOUT_S = 30.0
TOTAL_AGENT_BUDGET_S = 180.0

# Identical (tool, arguments) repeated this many times without new information means the
# agent is looping rather than reasoning. Terminates rather than burning the budget.
MAX_IDENTICAL_TOOL_CALLS = 2

# The loaded window must hold the largest permitted prompt and the tokens generated
# into it. If this ever fails, the runtime would silently truncate rather than refuse,
# so it is checked at import rather than left to a comment.
assert QWEN_NUM_CTX >= MAX_CONTEXT_TOKENS + MAX_OUTPUT_TOKENS, (
    f"QWEN_NUM_CTX={QWEN_NUM_CTX} cannot hold MAX_CONTEXT_TOKENS="
    f"{MAX_CONTEXT_TOKENS} plus MAX_OUTPUT_TOKENS={MAX_OUTPUT_TOKENS}; "
    "prompts would be silently truncated by Ollama."
)

# ------------------------------------------------------------------ agent states
STATE_OBSERVE = "OBSERVE"
STATE_ANALYZE = "ANALYZE"
STATE_REQUEST_TOOL = "REQUEST_TOOL"
STATE_REVIEW_RESULT = "REVIEW_RESULT"
STATE_SIMULATE = "SIMULATE"
STATE_ASSESS = "ASSESS"
STATE_PROPOSE = "PROPOSE"
STATE_POLICY_VALIDATE = "POLICY_VALIDATE"
STATE_REQUEST_APPROVAL = "REQUEST_APPROVAL"
STATE_UNCERTAIN = "UNCERTAIN"
STATE_ABANDONED = "ABANDONED"

# States the MODEL is allowed to emit. Deliberately narrower than the internal set:
# REVIEW_RESULT and POLICY_VALIDATE are driven by the loop after a tool returns, not
# claimed by the model, so the model cannot assert that a policy check happened.
MODEL_EMITTABLE_STATES = (
    STATE_OBSERVE,
    STATE_ANALYZE,
    STATE_REQUEST_TOOL,
    STATE_SIMULATE,
    STATE_ASSESS,
    STATE_PROPOSE,
    STATE_REQUEST_APPROVAL,
    STATE_UNCERTAIN,
    STATE_ABANDONED,
)

ALL_STATES = MODEL_EMITTABLE_STATES + (STATE_REVIEW_RESULT, STATE_POLICY_VALIDATE)

# ------------------------------------------------------------------ tools
TOOL_GET_INCIDENT_CONTEXT = "get_incident_context"
TOOL_GET_DETECTION_EVIDENCE = "get_detection_evidence"
TOOL_GET_GATEWAY_HEALTH = "get_gateway_health"
TOOL_GET_ROUTING_OPTIONS = "get_routing_options"
TOOL_RUN_COUNTERFACTUAL = "run_counterfactual"
TOOL_ESTIMATE_BUSINESS_IMPACT = "estimate_business_impact"
TOOL_CHECK_ACTION_BOUNDS = "check_action_bounds"
TOOL_PROPOSE_ACTION = "propose_action"
TOOL_REQUEST_HUMAN_APPROVAL = "request_human_approval"

TOOL_NAMES = (
    TOOL_CHECK_ACTION_BOUNDS,
    TOOL_ESTIMATE_BUSINESS_IMPACT,
    TOOL_GET_DETECTION_EVIDENCE,
    TOOL_GET_GATEWAY_HEALTH,
    TOOL_GET_INCIDENT_CONTEXT,
    TOOL_GET_ROUTING_OPTIONS,
    TOOL_PROPOSE_ACTION,
    TOOL_REQUEST_HUMAN_APPROVAL,
    TOOL_RUN_COUNTERFACTUAL,
)

# ------------------------------------------------------------------ state → tool allowlist
#
# A tool being implemented is not permission to call it. Access is gated on the state
# the agent is actually in, so (for example) `request_human_approval` is unreachable
# until a recommendation exists — the model cannot skip to asking for approval.
#
# Read-only context tools stay available throughout because re-reading evidence is
# always safe; the write-capable tools narrow sharply.
STATE_TOOL_ALLOWLIST: dict[str, tuple[str, ...]] = {
    STATE_OBSERVE: (TOOL_GET_INCIDENT_CONTEXT,),
    STATE_ANALYZE: (
        TOOL_GET_INCIDENT_CONTEXT,
        TOOL_GET_DETECTION_EVIDENCE,
        TOOL_GET_GATEWAY_HEALTH,
        TOOL_GET_ROUTING_OPTIONS,
    ),
    STATE_SIMULATE: (
        TOOL_GET_GATEWAY_HEALTH,
        TOOL_GET_ROUTING_OPTIONS,
        TOOL_RUN_COUNTERFACTUAL,
        TOOL_ESTIMATE_BUSINESS_IMPACT,
    ),
    STATE_ASSESS: (
        TOOL_ESTIMATE_BUSINESS_IMPACT,
        TOOL_CHECK_ACTION_BOUNDS,
    ),
    STATE_PROPOSE: (TOOL_PROPOSE_ACTION,),
    STATE_REQUEST_APPROVAL: (TOOL_REQUEST_HUMAN_APPROVAL,),
    # Terminal / non-tool states grant nothing.
    STATE_REQUEST_TOOL: (),
    STATE_UNCERTAIN: (),
    STATE_ABANDONED: (),
}

# ------------------------------------------------------------------ tool outcomes
OUTCOME_SUCCESS = "SUCCESS"
OUTCOME_NO_DATA = "NO_DATA"
OUTCOME_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
OUTCOME_INVALID_REQUEST = "INVALID_REQUEST"
OUTCOME_SAFETY_BLOCK = "SAFETY_BLOCK"
OUTCOME_TIMEOUT = "TIMEOUT"
OUTCOME_INTERNAL_ERROR = "INTERNAL_ERROR"

TOOL_OUTCOMES = (
    OUTCOME_INSUFFICIENT_EVIDENCE,
    OUTCOME_INTERNAL_ERROR,
    OUTCOME_INVALID_REQUEST,
    OUTCOME_NO_DATA,
    OUTCOME_SAFETY_BLOCK,
    OUTCOME_SUCCESS,
    OUTCOME_TIMEOUT,
)

# Retry budget per outcome. SAFETY_BLOCK is absent deliberately: retrying a safety
# refusal in the hope of a different answer is precisely how safety gets bypassed.
TOOL_RETRY_BUDGET: dict[str, int] = {
    OUTCOME_SUCCESS: 0,
    OUTCOME_NO_DATA: 0,
    OUTCOME_INSUFFICIENT_EVIDENCE: 0,
    OUTCOME_INVALID_REQUEST: 1,
    OUTCOME_SAFETY_BLOCK: 0,
    OUTCOME_TIMEOUT: 1,
    OUTCOME_INTERNAL_ERROR: 1,
}

# Tools for which a re-run is never permitted, even on a retryable outcome.
NEVER_RETRY_TOOLS = (TOOL_CHECK_ACTION_BOUNDS, TOOL_REQUEST_HUMAN_APPROVAL)

# ------------------------------------------------------------------ agent run status
RUN_RUNNING = "RUNNING"
RUN_SUCCEEDED = "SUCCEEDED"
RUN_AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"
RUN_BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
RUN_FAILED = "FAILED"

# ------------------------------------------------------------------ uncertainty
UNCERTAINTY_MISSING_EVIDENCE = "MISSING_EVIDENCE"
UNCERTAINTY_CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
UNCERTAINTY_TOOL_FAILURE = "TOOL_FAILURE"
UNCERTAINTY_SIMULATION_UNAVAILABLE = "SIMULATION_UNAVAILABLE"
UNCERTAINTY_LOW_CONFIDENCE = "LOW_CONFIDENCE"
UNCERTAINTY_POLICY_BLOCKED = "POLICY_BLOCKED"
UNCERTAINTY_MODEL_UNCERTAINTY = "MODEL_UNCERTAINTY"

UNCERTAINTY_KINDS = (
    UNCERTAINTY_CONFLICTING_EVIDENCE,
    UNCERTAINTY_LOW_CONFIDENCE,
    UNCERTAINTY_MISSING_EVIDENCE,
    UNCERTAINTY_MODEL_UNCERTAINTY,
    UNCERTAINTY_POLICY_BLOCKED,
    UNCERTAINTY_SIMULATION_UNAVAILABLE,
    UNCERTAINTY_TOOL_FAILURE,
)

UNCERTAINTY_LEVELS = ("LOW", "MEDIUM", "HIGH")

RESPONSE_REQUEST_TOOL = "REQUEST_TOOL"
RESPONSE_NO_ACTION = "NO_ACTION"
RESPONSE_ABANDON = "ABANDON"
UNCERTAINTY_RESPONSES = (RESPONSE_ABANDON, RESPONSE_NO_ACTION, RESPONSE_REQUEST_TOOL)

# ------------------------------------------------------------------ context selection
# Deterministic truncation. The same incident state must build the same context, so
# evidence is ranked by a stable key and cut at a fixed count — never sampled.
MAX_EVIDENCE_RECORDS = 20
MAX_GATEWAYS_IN_CONTEXT = 5
MAX_ALTERNATIVES_IN_CONTEXT = 5
MAX_REASONING_SUMMARY_CHARS = 600
