"""
Typed schemas and the model-output validation boundary.

THIS MODULE IS THE AIRLOCK.
---------------------------
Everything the model emits passes through `parse_agent_decision()` before any other
code sees it. Validation is deliberately strict and deliberately non-repairing:

  * unknown kind             → reject
  * unknown tool             → reject
  * unexpected field         → reject (not ignored — an unexpected field is evidence
                               the model is trying something the schema didn't plan for)
  * missing required field   → reject
  * wrong type               → reject
  * forbidden numeric field  → reject, with a distinct exception type

Nothing here coerces, clamps, rounds, or "best-effort fixes" model output. A malformed
decision is a failed turn, recorded as such. Silently repairing unsafe output is how an
invalid decision becomes an executed one.

TWO INDEPENDENT LAYERS, AND THEY DO DIFFERENT JOBS
---------------------------------------------------
`response_json_schema()` below is handed to Ollama for CONSTRAINED DECODING, which pins
the SHAPE at generation time. This module then validates MEANING: which tools exist,
which fields are forbidden, which types are real. Constrained decoding cannot express
"never supply `expected_gmv_retained`" or "cite only IDs a tool returned", so the
hand-written layer is retained in full and remains authoritative.

WHY VALIDATION IS HAND-WRITTEN
------------------------------
No pydantic/jsonschema dependency is added for this. The rules are few, exact, and
security-relevant, and a hand-written validator makes each refusal explicit and
greppable rather than hidden behind a library's coercion defaults (pydantic v1, for
instance, would happily coerce the string "20" into an int — precisely the silent
normalisation this boundary must not perform).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .constants import (
    MAX_REASONING_SUMMARY_CHARS,
    TOOL_NAMES,
    UNCERTAINTY_KINDS,
    UNCERTAINTY_LEVELS,
    UNCERTAINTY_RESPONSES,
)
from .errors import ForbiddenNumericField, ModelOutputInvalid

# ---------------------------------------------------------------------------
# Forbidden numeric fields
# ---------------------------------------------------------------------------
# If any of these appears ANYWHERE in a model-emitted tool-call payload, the call is
# refused. These are quantities only the deterministic layer may produce; a model that
# supplies one is attempting to substitute its own arithmetic for the simulator's.
#
# The check is on the KEY, not the value, so it fires whether the model sends a number,
# a numeric string, or null — there is no encoding that sneaks a forbidden field past.
FORBIDDEN_NUMERIC_FIELDS = frozenset(
    {
        "traffic_percentage",
        "expected_gmv",
        "expected_gmv_retained",
        "projected_gmv_retained",
        "projected_gmv_total",
        "projected_gmv_at_risk",
        "gmv",
        "gmv_retained",
        "expected_success_delta",
        "success_delta",
        "projected_success_rate",
        "expected_latency_delta_ms",
        "latency_delta_ms",
        "risk_score",
        "risk",
        "risk_components",
        "confidence",
        "evidence_strength",
        "significance_sigma",
        "severity",
        "concentration_after",
        "simulation_result",
        "projected_failure_count",
        "benefit",
        "expected_benefit",
        # Safety limits — a model naming one is trying to move a threshold.
        "threshold",
        "thresholds",
        "min_confidence",
        "max_traffic_shift",
        "max_concentration",
        "no_action_margin",
        "policy_version",
        "override",
        "force",
    }
)

# ---------------------------------------------------------------------------
# The minimal protocol
# ---------------------------------------------------------------------------
KIND_TOOL_CALL = "TOOL_CALL"
KIND_FINAL = "FINAL"
KINDS = (KIND_FINAL, KIND_TOOL_CALL)

DECISION_RECOMMEND = "RECOMMEND"
DECISION_NO_ACTION = "NO_ACTION"
DECISION_UNCERTAIN = "UNCERTAIN"
DECISIONS = (DECISION_NO_ACTION, DECISION_RECOMMEND, DECISION_UNCERTAIN)

_ALLOWED_TOP_LEVEL = frozenset(
    {
        "kind",
        "tool_name",
        "arguments",
        "decision",
        "simulation_id",
        "rationale",
        "evidence_ids",
        "uncertainty_kind",
        "uncertainty_level",
        "uncertainty_response",
    }
)


@dataclass(frozen=True)
class ToolCall:
    """One validated tool request. Arguments are validated again by the tool itself."""

    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Uncertainty:
    """Machine-readable uncertainty. Never prose, so a caller can branch on it."""

    kind: str
    level: str
    response: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "level": self.level,
            "response": self.response,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AgentDecision:
    """
    One schema-valid model turn, in the MINIMAL protocol.

    THE MODEL EMITS AN OPERATION. THE APPLICATION OWNS STATE.
    ---------------------------------------------------------
    The previous protocol required the model to declare its own agent state
    (OBSERVE / ANALYZE / SIMULATE / …). That field was never load-bearing — tool
    authorization has always derived the phase from persisted progress — so it was pure
    failure surface: the model had to produce it, could get it wrong (observed in real
    runs: "CHECK", "CHECK_BOUND"), and nothing consumed it. It is gone.

    What remains is the smallest set the model genuinely must choose: an operation
    (call a tool, or finish) and, if finishing, which persisted candidate.
    """

    kind: str                                   # TOOL_CALL | FINAL
    tool_call: ToolCall | None = None
    decision: str | None = None                 # RECOMMEND | NO_ACTION | UNCERTAIN
    simulation_id: int | None = None
    rationale: str = ""
    evidence_ids: tuple[int, ...] = ()
    uncertainty: Uncertainty | None = None

    @property
    def is_final(self) -> bool:
        return self.kind == KIND_FINAL

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "tool_call": (
                None
                if self.tool_call is None
                else {"tool_name": self.tool_call.tool_name,
                      "arguments": self.tool_call.arguments}
            ),
            "decision": self.decision,
            "simulation_id": self.simulation_id,
            # A short structured summary, never hidden chain-of-thought — with
            # `think:false` no chain-of-thought is generated in the first place.
            "rationale": self.rationale,
            "evidence_ids": list(self.evidence_ids),
            "uncertainty": None if self.uncertainty is None else self.uncertainty.as_dict(),
        }


def response_json_schema() -> dict:
    """
    The JSON Schema handed to Ollama for CONSTRAINED DECODING.

    Deliberately FLAT. The most frequent real failure was the model collapsing every
    top-level field inside a nested `tool_call` object; a flat schema makes that mistake
    unrepresentable rather than merely detectable. `tool_name` and `arguments` sit at
    the top level and are reassembled into a `ToolCall` after parsing.

    Every field is listed in `required` — including the nullable ones. Constrained
    decoding pins the key set, so requiring all keys and permitting `null` values is
    what actually fixes the shape; leaving fields optional lets the sampler omit them.
    Measured on qwen3:8b / Ollama 0.16.1: 8/8 conformance, versus 0/8 under
    `format:"json"`.
    """
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "tool_name": {"type": ["string", "null"], "enum": [*TOOL_NAMES, None]},
            "arguments": {"type": ["object", "null"]},
            "decision": {"type": ["string", "null"], "enum": [*DECISIONS, None]},
            "simulation_id": {"type": ["integer", "null"]},
            "rationale": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "integer"}},
            "uncertainty_kind": {"type": ["string", "null"],
                                 "enum": [*UNCERTAINTY_KINDS, None]},
            "uncertainty_level": {"type": ["string", "null"],
                                  "enum": [*UNCERTAINTY_LEVELS, None]},
            "uncertainty_response": {"type": ["string", "null"],
                                     "enum": [*UNCERTAINTY_RESPONSES, None]},
        },
        "required": [
            "kind", "tool_name", "arguments", "decision", "simulation_id",
            "rationale", "evidence_ids", "uncertainty_kind", "uncertainty_level",
            "uncertainty_response",
        ],
    }


def _require_int_list(value: Any, name: str) -> tuple[int, ...]:
    """
    Accept a list of genuine ints only.

    `bool` is explicitly rejected despite being an int subclass in Python — `True` as an
    evidence id is a type confusion, not a citation.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ModelOutputInvalid(
            f"{name} must be a list of integers, got {type(value).__name__}"
        )
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ModelOutputInvalid(f"{name} must contain integers, got {item!r}")
        if item < 0:
            raise ModelOutputInvalid(f"{name} must contain non-negative integers, got {item}")
        out.append(item)
    return tuple(out)


def assert_no_forbidden_fields(payload: Any, path: str = "arguments") -> None:
    """
    Recursively refuse any forbidden quantitative or safety key.

    Recursive on purpose: a model that learns `{"traffic_percentage": 20}` is refused
    will try `{"action": {"traffic_percentage": 20}}` next. Nesting is not an escape.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in FORBIDDEN_NUMERIC_FIELDS:
                raise ForbiddenNumericField(
                    f"{path}.{key} is a deterministic-only field; the agent may not "
                    "supply it. Quantitative values are read server-side from the "
                    "persisted simulation."
                )
            assert_no_forbidden_fields(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            assert_no_forbidden_fields(item, f"{path}[{i}]")


def parse_agent_decision(raw_text: str) -> AgentDecision:
    """
    Parse and validate one model turn. Raises on anything unexpected.

    Constrained decoding now pins the shape, but this layer is unchanged in strictness
    and still authoritative for meaning: unknown tools, forbidden numeric fields, and
    type confusion are refused here regardless of what the sampler produced. Nothing is
    coerced or repaired — a malformed decision is a failed turn.

    `raw_text` is never executed, never `eval`'d, and never interpolated into SQL or a
    shell command. It is parsed as JSON and checked field by field.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ModelOutputInvalid("model returned an empty response")

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ModelOutputInvalid(f"model output is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ModelOutputInvalid(
            f"model output must be a JSON object, got {type(payload).__name__}"
        )

    unknown = set(payload) - _ALLOWED_TOP_LEVEL
    if unknown:
        raise ModelOutputInvalid(f"model output has unknown fields: {sorted(unknown)}")

    kind = payload.get("kind")
    if kind not in KINDS:
        raise ModelOutputInvalid(f"kind {kind!r} must be one of {KINDS}")

    rationale = payload.get("rationale") or ""
    if not isinstance(rationale, str):
        raise ModelOutputInvalid("rationale must be a string")

    decision = payload.get("decision")
    if decision is not None and decision not in DECISIONS:
        raise ModelOutputInvalid(
            f"decision {decision!r} must be one of {DECISIONS} or null"
        )

    simulation_id = payload.get("simulation_id")
    if simulation_id is not None:
        if isinstance(simulation_id, bool) or not isinstance(simulation_id, int):
            raise ModelOutputInvalid("simulation_id must be an integer or null")
        if simulation_id < 0:
            raise ModelOutputInvalid("simulation_id must be non-negative")

    # ---- tool call ---------------------------------------------------------
    tool_call = None
    if kind == KIND_TOOL_CALL:
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str):
            raise ModelOutputInvalid("TOOL_CALL requires a tool_name")
        if tool_name not in TOOL_NAMES:
            # Covers both a hallucinated tool and an attempt to name something like
            # "execute_action" or "run_sql" that deliberately does not exist.
            raise ModelOutputInvalid(
                f"unknown tool {tool_name!r}; the registry is fixed and contains only "
                f"{TOOL_NAMES}"
            )
        arguments = payload.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ModelOutputInvalid("arguments must be an object or null")
        assert_no_forbidden_fields(arguments)
        tool_call = ToolCall(tool_name=tool_name, arguments=arguments)
    elif payload.get("tool_name"):
        raise ModelOutputInvalid("kind FINAL must not carry a tool_name")

    if kind == KIND_FINAL and decision is None:
        raise ModelOutputInvalid("kind FINAL requires a decision")

    # ---- uncertainty (flat; nesting was a real failure mode) ----------------
    uncertainty = None
    u_kind = payload.get("uncertainty_kind")
    if u_kind is not None:
        if u_kind not in UNCERTAINTY_KINDS:
            raise ModelOutputInvalid(
                f"uncertainty_kind {u_kind!r} not in {UNCERTAINTY_KINDS}"
            )
        u_level = payload.get("uncertainty_level") or "MEDIUM"
        if u_level not in UNCERTAINTY_LEVELS:
            raise ModelOutputInvalid(
                f"uncertainty_level {u_level!r} not in {UNCERTAINTY_LEVELS}"
            )
        u_response = payload.get("uncertainty_response") or "NO_ACTION"
        if u_response not in UNCERTAINTY_RESPONSES:
            raise ModelOutputInvalid(
                f"uncertainty_response {u_response!r} not in {UNCERTAINTY_RESPONSES}"
            )
        uncertainty = Uncertainty(
            kind=u_kind, level=u_level, response=u_response,
            detail=rationale[:MAX_REASONING_SUMMARY_CHARS],
        )

    return AgentDecision(
        kind=kind,
        tool_call=tool_call,
        decision=decision,
        simulation_id=simulation_id,
        rationale=rationale[:MAX_REASONING_SUMMARY_CHARS],
        evidence_ids=_require_int_list(payload.get("evidence_ids"), "evidence_ids"),
        uncertainty=uncertainty,
    )


# ---------------------------------------------------------------------------
# Tool input schemas — {tool_name: (required, optional)}
# ---------------------------------------------------------------------------
# ARGUMENT MINIMIZATION (Day 4B P1 fix).
#
# `incident_id` and `analysis_run_id` are GONE from every tool. The agent run is bound
# to exactly one incident and one analysis run, so the application injects them
# server-side. Requiring the model to echo IDs it had just been told achieved nothing
# except failure surface — and it did fail: qwen3:8b invented `analysis_run_id: 101`,
# which reached a database write and aborted the transaction mid-run. A value the model
# cannot supply cannot be hallucinated.
#
# Note what `propose_action` still does NOT accept: no percentage, no GMV, no risk, no
# confidence. There is no parameter through which a fabricated number could arrive.
TOOL_INPUT_SCHEMA: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "get_incident_context": (frozenset(), frozenset()),
    "get_detection_evidence": (frozenset(), frozenset({"evidence_ids"})),
    "get_gateway_health": (frozenset(), frozenset({"gateway_ids"})),
    "get_routing_options": (frozenset(), frozenset()),
    "run_counterfactual": (
        frozenset({"action_type"}),
        # `traffic_percentage` is intentionally ABSENT. The agent selects a bounded
        # candidate by name; it does not dial in an arbitrary number.
        frozenset({"target_gateway_id", "candidate_percentage"}),
    ),
    "estimate_business_impact": (frozenset({"simulation_id"}), frozenset()),
    "check_action_bounds": (frozenset({"simulation_id"}), frozenset()),
    "propose_action": (
        frozenset({"simulation_id"}),
        frozenset({"rationale", "supporting_evidence_ids"}),
    ),
    "request_human_approval": (frozenset({"recommendation_id"}), frozenset()),
}


def validate_tool_arguments(tool_name: str, arguments: dict) -> dict:
    """
    Check a tool payload against its schema. Returns the arguments unchanged on success.

    Unknown fields are refused rather than dropped: silently discarding an unexpected
    argument hides the fact that the model tried to pass something, which is exactly
    the signal a security review needs to see.
    """
    if tool_name not in TOOL_INPUT_SCHEMA:
        raise ModelOutputInvalid(f"no schema for tool {tool_name!r}")

    required, optional = TOOL_INPUT_SCHEMA[tool_name]
    provided = set(arguments)

    unknown = provided - required - optional
    if unknown:
        raise ModelOutputInvalid(f"{tool_name} received unknown arguments: {sorted(unknown)}")
    missing = required - provided
    if missing:
        raise ModelOutputInvalid(f"{tool_name} missing required arguments: {sorted(missing)}")

    assert_no_forbidden_fields(arguments, path=tool_name)
    return arguments
