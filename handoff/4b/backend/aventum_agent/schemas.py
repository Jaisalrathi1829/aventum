"""
Typed schemas and the model-output validation boundary.

THIS MODULE IS THE AIRLOCK.
---------------------------
Everything the model emits passes through `parse_agent_decision()` before any other
code sees it. Validation is deliberately strict and deliberately non-repairing:

  * unknown state            → reject
  * unknown tool             → reject
  * unexpected field         → reject (not ignored — an unexpected field is evidence
                               the model is trying something the schema didn't plan for)
  * missing required field   → reject
  * wrong type               → reject
  * forbidden numeric field  → reject, with a distinct exception type

Nothing here coerces, clamps, rounds, or "best-effort fixes" model output. A malformed
decision is a failed turn, recorded as such. Silently repairing unsafe output is how an
invalid decision becomes an executed one.

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
from dataclasses import dataclass, field
from typing import Any

from .constants import (
    MAX_REASONING_SUMMARY_CHARS,
    MODEL_EMITTABLE_STATES,
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
    """One schema-valid model turn."""

    state: str
    tool_call: ToolCall | None = None
    reasoning_summary: str = ""
    evidence_ids: tuple[int, ...] = ()
    simulation_ids: tuple[int, ...] = ()
    recommendation_intent: str | None = None
    uncertainty: Uncertainty | None = None

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "tool_call": (
                None
                if self.tool_call is None
                else {
                    "tool_name": self.tool_call.tool_name,
                    "arguments": self.tool_call.arguments,
                }
            ),
            # A short structured summary, never hidden chain-of-thought — with
            # `think:false` no chain-of-thought is generated in the first place.
            "reasoning_summary": self.reasoning_summary,
            "evidence_ids": list(self.evidence_ids),
            "simulation_ids": list(self.simulation_ids),
            "recommendation_intent": self.recommendation_intent,
            "uncertainty": None if self.uncertainty is None else self.uncertainty.as_dict(),
        }


_ALLOWED_TOP_LEVEL = frozenset(
    {
        "state",
        "tool_call",
        "reasoning_summary",
        "evidence_ids",
        "simulation_ids",
        "recommendation_intent",
        "uncertainty",
    }
)

_ALLOWED_INTENTS = frozenset({"REROUTE", "NO_ACTION", None})


def _require_int_list(value: Any, name: str) -> tuple[int, ...]:
    """
    Accept a list of genuine ints only.

    `bool` is explicitly rejected despite being an int subclass in Python — `True` as an
    evidence id is a type confusion, not a citation.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ModelOutputInvalid(f"{name} must be a list of integers, got {type(value).__name__}")
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


def _parse_uncertainty(raw: Any) -> Uncertainty | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ModelOutputInvalid("uncertainty must be an object or null")
    unknown = set(raw) - {"kind", "level", "response", "detail"}
    if unknown:
        raise ModelOutputInvalid(f"uncertainty has unknown fields: {sorted(unknown)}")

    kind = raw.get("kind")
    level = raw.get("level")
    response = raw.get("response")
    if kind not in UNCERTAINTY_KINDS:
        raise ModelOutputInvalid(f"uncertainty.kind {kind!r} not in {UNCERTAINTY_KINDS}")
    if level not in UNCERTAINTY_LEVELS:
        raise ModelOutputInvalid(f"uncertainty.level {level!r} not in {UNCERTAINTY_LEVELS}")
    if response not in UNCERTAINTY_RESPONSES:
        raise ModelOutputInvalid(f"uncertainty.response {response!r} not in {UNCERTAINTY_RESPONSES}")

    detail = raw.get("detail") or ""
    if not isinstance(detail, str):
        raise ModelOutputInvalid("uncertainty.detail must be a string")
    return Uncertainty(kind=kind, level=level, response=response,
                       detail=detail[:MAX_REASONING_SUMMARY_CHARS])


def _parse_tool_call(raw: Any) -> ToolCall | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ModelOutputInvalid("tool_call must be an object or null")

    unknown = set(raw) - {"tool_name", "arguments"}
    if unknown:
        raise ModelOutputInvalid(f"tool_call has unknown fields: {sorted(unknown)}")

    tool_name = raw.get("tool_name")
    if not isinstance(tool_name, str):
        raise ModelOutputInvalid("tool_call.tool_name must be a string")
    if tool_name not in TOOL_NAMES:
        # Covers both a hallucinated tool and an attempt to name something like
        # "execute_action" or "run_sql" that deliberately does not exist.
        raise ModelOutputInvalid(
            f"unknown tool {tool_name!r}; the registry is fixed and contains only {TOOL_NAMES}"
        )

    arguments = raw.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ModelOutputInvalid("tool_call.arguments must be an object")

    # Fires before any tool sees the payload.
    assert_no_forbidden_fields(arguments)
    return ToolCall(tool_name=tool_name, arguments=arguments)


def parse_agent_decision(raw_text: str) -> AgentDecision:
    """
    Parse and validate one model turn. Raises on anything unexpected.

    `raw_text` is whatever the model returned. It is never executed, never eval'd, and
    never interpolated into SQL or a shell command — it is parsed as JSON and checked
    field by field.
    """
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ModelOutputInvalid("model returned an empty response")

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ModelOutputInvalid(f"model output is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ModelOutputInvalid(f"model output must be a JSON object, got {type(payload).__name__}")

    unknown = set(payload) - _ALLOWED_TOP_LEVEL
    if unknown:
        raise ModelOutputInvalid(f"model output has unknown fields: {sorted(unknown)}")

    state = payload.get("state")
    if state not in MODEL_EMITTABLE_STATES:
        raise ModelOutputInvalid(
            f"state {state!r} is not one the model may emit; allowed: {MODEL_EMITTABLE_STATES}"
        )

    summary = payload.get("reasoning_summary") or ""
    if not isinstance(summary, str):
        raise ModelOutputInvalid("reasoning_summary must be a string")

    intent = payload.get("recommendation_intent")
    if intent not in _ALLOWED_INTENTS:
        raise ModelOutputInvalid(
            f"recommendation_intent {intent!r} must be REROUTE, NO_ACTION, or null"
        )

    decision = AgentDecision(
        state=state,
        tool_call=_parse_tool_call(payload.get("tool_call")),
        reasoning_summary=summary[:MAX_REASONING_SUMMARY_CHARS],
        evidence_ids=_require_int_list(payload.get("evidence_ids"), "evidence_ids"),
        simulation_ids=_require_int_list(payload.get("simulation_ids"), "simulation_ids"),
        recommendation_intent=intent,
        uncertainty=_parse_uncertainty(payload.get("uncertainty")),
    )

    # A REQUEST_TOOL turn that names no tool is incoherent; so is a terminal state that
    # tries to call one.
    if decision.state == "REQUEST_TOOL" and decision.tool_call is None:
        raise ModelOutputInvalid("state REQUEST_TOOL requires a tool_call")
    if decision.state in ("UNCERTAIN", "ABANDONED") and decision.tool_call is not None:
        raise ModelOutputInvalid(f"state {decision.state} must not carry a tool_call")

    return decision


# ---------------------------------------------------------------------------
# Tool input schemas — {tool_name: (required, optional)}
# ---------------------------------------------------------------------------
# Exactly the contract's fields. Note what `propose_action` does NOT accept: no
# percentage, no GMV, no risk, no confidence. There is no parameter through which a
# fabricated number could arrive, which is the structural guarantee, not a check.
TOOL_INPUT_SCHEMA: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "get_incident_context": (frozenset({"analysis_run_id"}), frozenset()),
    "get_detection_evidence": (frozenset({"analysis_run_id"}), frozenset({"evidence_ids"})),
    "get_gateway_health": (frozenset({"incident_id"}), frozenset({"gateway_ids"})),
    "get_routing_options": (frozenset({"incident_id"}), frozenset()),
    "run_counterfactual": (
        frozenset({"incident_id", "analysis_run_id", "action_type"}),
        # `traffic_percentage` is intentionally ABSENT from both sets. The agent selects
        # a bounded candidate by name; it does not dial in an arbitrary number.
        frozenset({"source_gateway_id", "target_gateway_id", "candidate_percentage"}),
    ),
    "estimate_business_impact": (frozenset({"simulation_id"}), frozenset()),
    "check_action_bounds": (frozenset({"simulation_id", "analysis_run_id"}), frozenset()),
    "propose_action": (
        frozenset({"simulation_id", "analysis_run_id"}),
        frozenset({"rationale", "supporting_evidence_ids", "alternatives_considered"}),
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
        raise ModelOutputInvalid(
            f"{tool_name} received unknown arguments: {sorted(unknown)}"
        )
    missing = required - provided
    if missing:
        raise ModelOutputInvalid(f"{tool_name} missing required arguments: {sorted(missing)}")

    assert_no_forbidden_fields(arguments, path=tool_name)
    return arguments
