"""
The bounded agent control loop.

    OBSERVE → ANALYZE → REQUEST_TOOL ⇄ REVIEW_RESULT → SIMULATE → ASSESS
            → PROPOSE → POLICY_VALIDATE → REQUEST_APPROVAL → STOP

THE LOOP OWNS THE STATE, NOT THE MODEL
---------------------------------------
The model *proposes* a next state; the loop decides whether that transition is allowed
and which tools are reachable from it. A model that emits `REQUEST_APPROVAL` on turn 1
does not get approval tools — it gets a rejection and a corrective message. This is why
the tool allowlist is keyed on the loop's state rather than on the model's claim.

Execution is not a state. There is no branch, no flag, and no tool that reaches the
execution adapter; the loop terminates at REQUEST_APPROVAL and returns.

EVERY BUDGET IS CHECKED BEFORE THE WORK, NOT AFTER
---------------------------------------------------
Turn, tool-call, simulation, and wall-clock limits are all tested before the action they
bound. Exceeding one produces `BUDGET_EXCEEDED` and a terminated run — never a
truncated-but-accepted decision, and never a fabricated fallback recommendation.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from aventum_action.audit import ACTOR_AGENT, ACTOR_SYSTEM, emit, ref
from aventum_counterfactual.constants import ACTION_NO_ACTION, STATUS_VALID
from aventum_counterfactual.models import AgentRun, AgentToolCall, CounterfactualSimulation
from aventum_counterfactual.source import WorldState
from aventum_incident.models import IncidentAnomaly

from . import AGENT_MODEL_VERSION
from .client import ModelResponse, OllamaClient
from .constants import (
    MAX_CONTEXT_TOKENS,
    MAX_IDENTICAL_TOOL_CALLS,
    MAX_SIMULATIONS,
    MAX_TOOL_CALLS,
    MAX_TURNS,
    NEVER_RETRY_TOOLS,
    OUTCOME_SAFETY_BLOCK,
    RUN_AGENT_UNAVAILABLE,
    RUN_BUDGET_EXCEEDED,
    RUN_FAILED,
    RUN_RUNNING,
    RUN_SUCCEEDED,
    STATE_ABANDONED,
    STATE_ANALYZE,
    STATE_ASSESS,
    STATE_OBSERVE,
    STATE_PROPOSE,
    STATE_REQUEST_APPROVAL,
    STATE_REQUEST_TOOL,
    STATE_SIMULATE,
    STATE_TOOL_ALLOWLIST,
    STATE_UNCERTAIN,
    TOTAL_AGENT_BUDGET_S,
)
from .context import AgentContext, build_agent_context
from .errors import AgentUnavailable, ModelOutputInvalid
from .prompts import (
    SCHEMA_REMINDER,
    SYSTEM_PROMPT_VERSION,
    build_system_prompt,
    prompt_fingerprint_material,
)
from .schemas import AgentDecision, parse_agent_decision
from .tools import ToolContext, ToolResult, dispatch

# Blank line between a rejection reason and the re-stated schema.
SCHEMA_SEP = "\n\n"

# An identical malformed response this many times means the model is stuck in a
# formatting mode, not converging. Terminate instead of spending the whole turn budget
# on the same rejection.
MAX_IDENTICAL_PARSE_FAILURES = 2

# Consecutive turns that call no tool and reach no conclusion. Three is generous:
# one is a stumble, two is a pattern, three is a stall.
MAX_IDLE_TURNS = 3


def _targeted_correction(error: str) -> str:
    """
    Add a pointed hint for formatting mistakes we have actually observed.

    Generic "return valid JSON" feedback did not move qwen3:8b off either of the two
    failure modes seen in real runs — a generic {"action","reason"} envelope, and
    collapsing every top-level field inside `tool_call`. Naming the specific mistake and
    showing the correct nesting is ordinary prompt repair, and it changes only what the
    model is told, never what the validator accepts.
    """
    if "tool_call has unknown fields" in error:
        return (
            "MISTAKE: you put top-level fields INSIDE tool_call. `tool_call` contains "
            "ONLY {\"tool_name\", \"arguments\"}. Every other field — state, "
            "reasoning_summary, evidence_ids, simulation_ids, recommendation_intent, "
            "uncertainty — is a SIBLING of tool_call, not a child of it.\n\n"
        )
    if "unknown fields" in error:
        return (
            "MISTAKE: you invented top-level keys. Use exactly the seven keys below "
            "and no others.\n\n"
        )
    if "recommendation_intent" in error:
        return (
            "MISTAKE: recommendation_intent is a KIND of action, not a candidate name. "
            "Use exactly \"REROUTE\" or \"NO_ACTION\" or null. Put the specific "
            "simulation you chose in simulation_ids instead.\n\n"
        )
    if "is not one the model may emit" in error:
        return (
            "MISTAKE: you invented a state name. Use exactly one of OBSERVE, ANALYZE, "
            "REQUEST_TOOL, SIMULATE, ASSESS, PROPOSE, REQUEST_APPROVAL, UNCERTAIN, "
            "ABANDONED. To run a policy check, use state ASSESS with tool_call "
            "check_action_bounds.\n\n"
        )
    return ""

# Tool authorization is keyed on the LOOP'S PHASE, derived from what has actually been
# accomplished — never on the state the model claims to be in.
#
# This distinction is the security property. If permissions followed the model's
# self-declared `state`, a model could unlock `request_human_approval` on turn 1 simply
# by asserting `"state": "REQUEST_APPROVAL"`. Deriving the phase from persisted progress
# means approval tools are unreachable until a recommendation genuinely exists, whatever
# the model calls itself.
#
# The model's `state` is still recorded, and still validated against the schema — it is
# just not load-bearing for authorization.
PHASE_ANALYZE = "ANALYZE"
PHASE_ASSESS = "ASSESS"
PHASE_PROPOSE = "PROPOSE"
PHASE_REQUEST_APPROVAL = "REQUEST_APPROVAL"

# Cumulative: later phases retain earlier read-only tools, because re-reading evidence
# is always safe and forcing a model to "unlearn" a tool creates pointless rejections.
_PHASE_TOOLS: dict[str, tuple[str, ...]] = {
    PHASE_ANALYZE: (
        "get_incident_context", "get_detection_evidence", "get_gateway_health",
        "get_routing_options", "run_counterfactual",
    ),
    PHASE_ASSESS: (
        "get_incident_context", "get_detection_evidence", "get_gateway_health",
        "get_routing_options", "run_counterfactual", "estimate_business_impact",
        "check_action_bounds",
    ),
    PHASE_PROPOSE: (
        "get_incident_context", "get_detection_evidence", "get_gateway_health",
        "get_routing_options", "run_counterfactual", "estimate_business_impact",
        "check_action_bounds", "propose_action",
    ),
    PHASE_REQUEST_APPROVAL: (
        "get_incident_context", "get_detection_evidence", "get_gateway_health",
        "get_routing_options", "estimate_business_impact", "check_action_bounds",
        "propose_action", "request_human_approval",
    ),
}


def primary_alert_role_for(session: Session, analysis_run_id: int) -> str | None:
    """PRIMARY only when a PRIMARY alert actually exists; never promotes a derivative."""
    row = session.scalar(
        select(IncidentAnomaly).where(
            IncidentAnomaly.analysis_run_id == analysis_run_id,
            IncidentAnomaly.suppressed.is_(False),
            IncidentAnomaly.alert_role == "PRIMARY",
        )
    )
    return "PRIMARY" if row is not None else None


@dataclass
class TurnRecord:
    """One model turn, for audit and replay. Never holds chain-of-thought."""

    index: int
    state: str
    decision: dict
    model: dict
    tool_result: dict | None = None
    rejection: str | None = None


@dataclass
class AgentOutcome:
    """The full result of one bounded agent run."""

    agent_run_id: int | None
    status: str
    final_state: str
    recommendation_id: int | None = None
    approval_id: int | None = None
    selected_simulation_id: int | None = None
    turns: list[TurnRecord] = field(default_factory=list)
    tool_calls_used: int = 0
    simulations_used: int = 0
    context_tokens_estimate: int = 0
    prompt_tokens_total: int | None = None
    output_tokens_total: int | None = None
    elapsed_ms: float = 0.0
    qwen_latencies_ms: list[float] = field(default_factory=list)
    agent_run_fingerprint: str = ""
    error: str | None = None
    uncertainty: dict | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == RUN_SUCCEEDED


class AgentLoop:
    """Bounded, auditable orchestration of one incident."""

    def __init__(
        self,
        session: Session,
        client: OllamaClient,
        world: WorldState,
        context: AgentContext,
        incident_id: int,
        analysis_run_id: int,
    ) -> None:
        self.session = session
        self.client = client
        self.world = world
        self.context = context
        self.incident_id = incident_id
        self.analysis_run_id = analysis_run_id

        self.tool_ctx = ToolContext(
            session=session, incident_id=incident_id,
            analysis_run_id=analysis_run_id, world=world,
        )
        # Citable IDs grow only as tools expose them, so a fabricated reference is
        # detectable rather than plausible.
        self.seen_evidence_ids: set[int] = set(context.valid_evidence_ids)
        self.seen_simulation_ids: set[int] = set()
        self.seen_recommendation_ids: set[int] = set()
        # Simulations that have PASSED the deterministic policy gate. Only these
        # unlock `propose_action` -- the agent cannot propose an unvalidated candidate.
        self._permitted_simulation_ids: set[int] = set()
        # Reroute candidates actually simulated. Concluding "NO_ACTION is best" is a
        # COMPARATIVE claim, so it requires at least one alternative to compare against.
        self._reroute_simulations: set[int] = set()
        # Set when get_routing_options shows no viable target -- the other honest
        # route to a NO_ACTION conclusion, since there is nothing to compare.
        self._no_viable_target_established = False
        self._call_signatures: dict[str, int] = {}
        self._parse_failures: dict[str, int] = {}
        self._idle_turns = 0
        self._blocked_signatures: set[str] = set()

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _signature(tool_name: str, arguments: dict) -> str:
        return hashlib.sha256(
            f"{tool_name}|{json.dumps(arguments, sort_keys=True, default=str)}".encode()
        ).hexdigest()

    def current_phase(self) -> str:
        """
        Derive the phase from real progress, not from what the model says it is doing.

        A recommendation must exist before approval tools appear; a PERMITTED simulation
        must exist before `propose_action` appears; a simulation must exist before the
        assessment tools appear. Each gate is a fact about persisted state, so no
        assertion in model output can advance it.
        """
        if self.seen_recommendation_ids:
            return PHASE_REQUEST_APPROVAL
        if self._permitted_simulation_ids:
            return PHASE_PROPOSE
        if self.seen_simulation_ids:
            return PHASE_ASSESS
        return PHASE_ANALYZE

    def _authorized(self, tool_name: str) -> tuple[bool, str]:
        phase = self.current_phase()
        allowed = _PHASE_TOOLS[phase]
        if tool_name not in allowed:
            # The message explains what is missing rather than only what is refused, so
            # a cooperative model can correct itself instead of guessing.
            need = {
                "propose_action": "run check_action_bounds on a VALID simulation first",
                "request_human_approval": "call propose_action first",
                "estimate_business_impact": "run a counterfactual first",
                "check_action_bounds": "run a counterfactual first",
            }.get(tool_name, "call the prerequisite tools first")
            return False, (
                f"tool {tool_name} is not available yet (phase {phase}): {need}. "
                f"Available now: {list(allowed)}"
            )
        return True, ""

    def _validate_citations(self, decision: AgentDecision) -> str | None:
        """Refuse invented evidence/simulation IDs before they can reach a record."""
        bad_ev = [i for i in decision.evidence_ids if i not in self.seen_evidence_ids]
        if bad_ev:
            return (
                f"evidence_ids {bad_ev} were never returned by a tool in this run; "
                "cite only IDs you have seen"
            )
        bad_sim = [i for i in decision.simulation_ids if i not in self.seen_simulation_ids]
        if bad_sim:
            return (
                f"simulation_ids {bad_sim} were never returned by a tool in this run; "
                "call run_counterfactual first"
            )
        return None

    def _no_action_is_justified(self) -> tuple[bool, str]:
        """
        Whether concluding NO_ACTION is supported by what the run actually did.

        Two legitimate routes:
          * at least one REROUTE candidate was simulated and compared, or
          * routing options were inspected and no viable target exists (so there is
            nothing to compare against, and NO_ACTION is the only honest answer).

        This guard makes the agent do the comparison rather than assert the conclusion.
        It never pushes toward acting: a compared NO_ACTION is accepted immediately.
        """
        if self._reroute_simulations:
            return True, ""
        if self._no_viable_target_established:
            return True, ""
        return False, (
            "NO_ACTION is a comparative conclusion and cannot be accepted yet: no "
            "REROUTE candidate has been simulated. Either call get_routing_options to "
            "establish that no viable target exists, or run_counterfactual on a bounded "
            "REROUTE candidate (10, 20, or 30 percent) so there is something to compare "
            "the baseline against."
        )

    def _record_exposure(self, result: ToolResult) -> None:
        self.seen_evidence_ids.update(result.exposed_evidence_ids)
        self.seen_simulation_ids.update(result.exposed_simulation_ids)
        self.seen_recommendation_ids.update(result.exposed_recommendation_ids)

    # -- the loop --------------------------------------------------------------
    def run(self) -> AgentOutcome:
        started = time.perf_counter()
        outcome = AgentOutcome(
            agent_run_id=None, status=RUN_RUNNING, final_state=STATE_OBSERVE,
            context_tokens_estimate=self.context.estimated_tokens(),
        )

        # Context budget is checked BEFORE the first call: sending an over-budget
        # prompt and discovering it afterwards wastes a turn and muddies the record.
        if outcome.context_tokens_estimate > MAX_CONTEXT_TOKENS:
            outcome.status = RUN_BUDGET_EXCEEDED
            outcome.final_state = STATE_ABANDONED
            outcome.error = (
                f"initial context ~{outcome.context_tokens_estimate} tokens exceeds "
                f"the {MAX_CONTEXT_TOKENS} budget"
            )
            return outcome

        run_row = AgentRun(
            incident_id=self.incident_id, analysis_run_id=self.analysis_run_id,
            status=RUN_RUNNING, model_name=self.client.model,
            model_options=self.client.runtime_config(),
            context_tokens_max=outcome.context_tokens_estimate,
        )
        self.session.add(run_row)
        self.session.flush()
        outcome.agent_run_id = run_row.agent_run_id

        emit(self.session, event_type="AGENT_RUN_STARTED", actor=ACTOR_SYSTEM,
             incident_id=self.incident_id,
             output_ref=ref("agent_runs", run_row.agent_run_id),
             payload={"model": self.client.model,
                      "options": self.client.runtime_config(),
                      "prompt_version": SYSTEM_PROMPT_VERSION,
                      "context_fingerprint": self.context.context_fingerprint})

        system_prompt = build_system_prompt()
        messages: list[dict] = [{
            "role": "user",
            "content": (
                "Incident context (deterministic, from Day 3 analysis):\n"
                + self.context.as_json()
                + "\n\nA NO_ACTION baseline has already been simulated for this incident. "
                  "Decide your next step and respond with the JSON schema."
            ),
        }]

        prompt_tokens = 0
        output_tokens = 0
        ordered_results: list[str] = []

        for turn in range(1, MAX_TURNS + 1):
            if (time.perf_counter() - started) > TOTAL_AGENT_BUDGET_S:
                return self._finish(outcome, run_row, RUN_BUDGET_EXCEEDED, STATE_ABANDONED,
                                    started, "total agent time budget exceeded",
                                    ordered_results)

            # ---- model turn ----------------------------------------------------
            try:
                response: ModelResponse = self.client.complete(system_prompt, messages)
            except AgentUnavailable as exc:
                return self._finish(outcome, run_row, RUN_AGENT_UNAVAILABLE, STATE_ABANDONED,
                                    started, str(exc), ordered_results)

            outcome.qwen_latencies_ms.append(response.latency_ms)
            prompt_tokens += response.prompt_tokens or 0
            output_tokens += response.output_tokens or 0

            try:
                decision = parse_agent_decision(response.text)
            except ModelOutputInvalid as exc:
                # Never silently repaired. Rejected, recorded, and corrected once.
                outcome.turns.append(TurnRecord(
                    index=turn, state="INVALID", decision={},
                    model=response.as_dict(), rejection=str(exc)))

                # A model that makes the SAME formatting mistake repeatedly is not going
                # to self-correct; it is burning the budget. Terminate rather than spend
                # every remaining turn on identical rejections — the same reasoning as
                # the identical-tool-call guard, applied to malformed output.
                signature = str(exc)[:200]
                self._parse_failures[signature] = self._parse_failures.get(signature, 0) + 1
                if self._parse_failures[signature] >= MAX_IDENTICAL_PARSE_FAILURES:
                    return self._finish(
                        outcome, run_row, RUN_FAILED, STATE_ABANDONED, started,
                        f"model repeated the same invalid output "
                        f"{self._parse_failures[signature]} times: {exc}",
                        ordered_results)

                messages.append({"role": "assistant", "content": response.text[:800]})
                messages.append({"role": "user", "content":
                                 f"Your response was rejected: {exc}\n\n"
                                 f"{_targeted_correction(str(exc))}{SCHEMA_REMINDER}"})
                if turn >= MAX_TURNS:
                    return self._finish(outcome, run_row, RUN_FAILED, STATE_ABANDONED,
                                        started, f"model output invalid: {exc}",
                                        ordered_results)
                continue

            record = TurnRecord(index=turn, state=decision.state,
                                decision=decision.as_dict(), model=response.as_dict())
            messages.append({"role": "assistant",
                             "content": json.dumps(decision.as_dict(), default=str)})

            # ---- citation grounding -------------------------------------------
            citation_error = self._validate_citations(decision)
            if citation_error:
                record.rejection = citation_error
                outcome.turns.append(record)
                messages.append({"role": "user", "content":
                                 f"Rejected: {citation_error}" + SCHEMA_SEP + SCHEMA_REMINDER})
                continue

            # ---- terminal states ------------------------------------------------
            if decision.state in (STATE_UNCERTAIN, STATE_ABANDONED):
                outcome.turns.append(record)
                outcome.uncertainty = (
                    decision.uncertainty.as_dict() if decision.uncertainty else None
                )
                status = RUN_SUCCEEDED if decision.state == STATE_UNCERTAIN else RUN_FAILED
                return self._finish(outcome, run_row, status, decision.state, started,
                                    None, ordered_results)

            # ---- no tool requested: nudge forward ------------------------------
            if decision.tool_call is None:
                if decision.recommendation_intent == ACTION_NO_ACTION:
                    # "NO_ACTION is best" is a COMPARATIVE claim. Accepting it without a
                    # single alternative having been simulated would let the agent skip
                    # the work and still sound decisive — the mirror image of fabricating
                    # a benefit. So it must either have compared something, or have
                    # established that no viable target exists.
                    justified, why = self._no_action_is_justified()
                    if not justified:
                        record.rejection = why
                        outcome.turns.append(record)
                        messages.append({"role": "user", "content":
                                         f"Rejected: {why}"})
                        continue
                    outcome.uncertainty = (
                        decision.uncertainty.as_dict() if decision.uncertainty else None
                    )
                    outcome.turns.append(record)
                    return self._finish(outcome, run_row, RUN_SUCCEEDED, STATE_ASSESS,
                                        started, None, ordered_results)
                outcome.turns.append(record)

                # A turn that calls no tool and reaches no conclusion makes no progress.
                # Repeating it is the same pathology as repeating a tool call, so it gets
                # the same treatment: terminate rather than spend the remaining budget
                # restating an identical summary.
                self._idle_turns += 1
                if self._idle_turns >= MAX_IDLE_TURNS:
                    return self._finish(
                        outcome, run_row, RUN_BUDGET_EXCEEDED, STATE_ABANDONED, started,
                        f"{self._idle_turns} consecutive turns made no progress: no tool "
                        "called and no conclusion reached",
                        ordered_results)

                messages.append({"role": "user", "content":
                                 "That turn made no progress: you called no tool and "
                                 "reached no conclusion. You must now EITHER call "
                                 "check_action_bounds on your chosen simulation_id, OR "
                                 "set recommendation_intent to \"NO_ACTION\" with "
                                 "tool_call null, OR report uncertainty. Do not restate "
                                 "your analysis."})
                continue

            # ---- budgets --------------------------------------------------------
            if outcome.tool_calls_used >= MAX_TOOL_CALLS:
                return self._finish(outcome, run_row, RUN_BUDGET_EXCEEDED, STATE_ABANDONED,
                                    started, f"tool-call budget ({MAX_TOOL_CALLS}) exhausted",
                                    ordered_results)

            call = decision.tool_call
            if (call.tool_name == "run_counterfactual"
                    and outcome.simulations_used >= MAX_SIMULATIONS):
                record.rejection = f"simulation budget ({MAX_SIMULATIONS}) exhausted"
                outcome.turns.append(record)
                messages.append({"role": "user", "content":
                                 f"Rejected: {record.rejection}. Decide using the "
                                 "simulations you already have."})
                continue

            # ---- authorization ---------------------------------------------------
            ok, why = self._authorized(call.tool_name)
            if not ok:
                record.rejection = why
                outcome.turns.append(record)
                messages.append({"role": "user", "content":
                                 f"Rejected: {why}" + SCHEMA_SEP + SCHEMA_REMINDER})
                continue

            signature = self._signature(call.tool_name, call.arguments)

            # A safety refusal is never re-attempted, even if the model asks again.
            if signature in self._blocked_signatures:
                record.rejection = "SAFETY_BLOCK already returned for this exact request"
                outcome.turns.append(record)
                messages.append({"role": "user", "content":
                                 "Rejected: this exact request was already BLOCKED by "
                                 "policy. A safety block cannot be retried. Consider "
                                 "another candidate or NO_ACTION."})
                continue

            repeats = self._call_signatures.get(signature, 0)
            if repeats >= MAX_IDENTICAL_TOOL_CALLS:
                return self._finish(outcome, run_row, RUN_BUDGET_EXCEEDED, STATE_ABANDONED,
                                    started,
                                    f"identical call to {call.tool_name} repeated "
                                    f"{repeats} times — looping, not progressing",
                                    ordered_results)
            self._call_signatures[signature] = repeats + 1

            # ---- dispatch --------------------------------------------------------
            self._idle_turns = 0
            result = dispatch(self.tool_ctx, call.tool_name, call.arguments)
            outcome.tool_calls_used += 1
            if call.tool_name == "run_counterfactual" and not result.error:
                outcome.simulations_used += 1
            elif call.tool_name == "run_counterfactual":
                outcome.simulations_used += 1

            if result.outcome == OUTCOME_SAFETY_BLOCK or call.tool_name in NEVER_RETRY_TOOLS:
                self._blocked_signatures.add(signature)

            self._record_exposure(result)
            if call.tool_name == "get_routing_options" and result.ok:
                options = result.authoritative.get("options") or []
                if not any(o.get("viable_target") for o in options):
                    self._no_viable_target_established = True
            if call.tool_name == "check_action_bounds" and result.ok:
                sid = result.authoritative.get("simulation_id")
                if sid is not None:
                    self._permitted_simulation_ids.add(int(sid))
            if (call.tool_name == "run_counterfactual"
                    and result.authoritative.get("action_type") == "REROUTE"
                    and result.authoritative.get("status") == STATUS_VALID):
                self._reroute_simulations.add(int(result.authoritative["simulation_id"]))
            self._persist_tool_call(run_row.agent_run_id, outcome.tool_calls_used,
                                    call, result)
            ordered_results.append(
                f"{call.tool_name}:{result.outcome}:"
                f"{json.dumps(result.authoritative, sort_keys=True, default=str)}"
            )

            record.tool_result = result.as_audit_record()
            outcome.turns.append(record)

            # Track outputs that move the run forward.
            if call.tool_name == "propose_action" and result.ok:
                outcome.recommendation_id = result.authoritative.get("recommendation_id")
                outcome.selected_simulation_id = call.arguments.get("simulation_id")
            if call.tool_name == "request_human_approval" and result.ok:
                outcome.approval_id = result.authoritative.get("approval_id")
                # The agent's authority ends here. There is no execution step.
                return self._finish(outcome, run_row, RUN_SUCCEEDED,
                                    STATE_REQUEST_APPROVAL, started, None, ordered_results)

            # Tool result goes back in the `tool` role — data, never instruction.
            messages.append({"role": "tool",
                             "content": json.dumps(result.as_model_payload(), default=str)})

        return self._finish(outcome, run_row, RUN_BUDGET_EXCEEDED, STATE_ABANDONED,
                            started, f"turn budget ({MAX_TURNS}) exhausted", ordered_results)

    # -- persistence -----------------------------------------------------------
    def _persist_tool_call(self, agent_run_id: int, sequence: int, call, result: ToolResult):
        self.session.add(AgentToolCall(
            agent_run_id=agent_run_id, sequence=sequence, tool_name=call.tool_name,
            request=call.arguments, response=result.as_audit_record(),
            outcome=result.outcome, latency_ms=round(result.latency_ms, 3), attempt=1,
        ))
        self.session.flush()
        emit(self.session, event_type="TOOL_CALLED", actor=ACTOR_AGENT,
             incident_id=self.incident_id,
             input_ref=ref("agent_runs", agent_run_id),
             payload={"sequence": sequence, "tool": call.tool_name,
                      "outcome": result.outcome,
                      "latency_ms": round(result.latency_ms, 1)})

    def _finish(self, outcome: AgentOutcome, run_row: AgentRun, status: str,
                final_state: str, started: float, error: str | None,
                ordered_results: list[str]) -> AgentOutcome:
        outcome.status = status
        outcome.final_state = final_state
        outcome.error = error
        outcome.elapsed_ms = (time.perf_counter() - started) * 1000.0
        outcome.simulations_used = max(outcome.simulations_used,
                                       self.tool_ctx.simulations_used)
        outcome.prompt_tokens_total = sum(
            t.model.get("prompt_tokens") or 0 for t in outcome.turns) or None
        outcome.output_tokens_total = sum(
            t.model.get("output_tokens") or 0 for t in outcome.turns) or None
        outcome.agent_run_fingerprint = self._fingerprint(outcome, ordered_results)

        run_row.status = status
        run_row.turns_used = len(outcome.turns)
        run_row.tool_calls_used = outcome.tool_calls_used
        run_row.simulations_used = outcome.simulations_used
        run_row.finished_at = datetime.now(timezone.utc)
        run_row.error_message = error
        self.session.flush()

        emit(self.session, event_type="AGENT_RUN_FINISHED", actor=ACTOR_SYSTEM,
             incident_id=self.incident_id,
             input_ref=ref("agent_runs", run_row.agent_run_id),
             payload={"status": status, "final_state": final_state,
                      "turns": len(outcome.turns),
                      "tool_calls": outcome.tool_calls_used,
                      "simulations": outcome.simulations_used,
                      "recommendation_id": outcome.recommendation_id,
                      "approval_id": outcome.approval_id,
                      "error": error},
             fingerprint=outcome.agent_run_fingerprint)
        return outcome

    def _fingerprint(self, outcome: AgentOutcome, ordered_results: list[str]) -> str:
        """
        Identity of the run's DECISION, not of its prose.

        Built from model, configuration, prompt version, initial context, ordered tool
        results, and the final structured output. Natural-language wording is excluded
        deliberately: at temperature 0 the structured decision is what must be stable,
        and treating a reworded summary as corruption would make replay useless.
        """
        material = "|".join([
            self.client.model,
            json.dumps(self.client.runtime_config(), sort_keys=True),
            prompt_fingerprint_material(),
            self.context.context_fingerprint,
            "||".join(ordered_results),
            str(outcome.recommendation_id),
            str(outcome.selected_simulation_id),
            outcome.final_state,
            outcome.status,
        ])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
