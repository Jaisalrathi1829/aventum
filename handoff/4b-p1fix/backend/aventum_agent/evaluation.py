"""
Offline agent replay and evaluation.

======================================================================
GROUND-TRUTH BOUNDARY — READ THIS BEFORE ADDING ANYTHING TO THIS FILE
======================================================================
This module is the ONLY place in the agent layer permitted to read incident ground
truth, and it may do so ONLY for scoring a run that has ALREADY finished.

The boundary is enforced three ways, not one:

  1. Nothing in `loop.py`, `tools.py`, `context.py`, `client.py`, `service.py`, or
     `prompts.py` imports this module. Inference has no path to it.
  2. Ground truth is read through `score_against_ground_truth()`, which takes a
     COMPLETED `AgentOutcome`. There is no signature by which it could influence a
     decision that has not happened yet.
  3. A test asserts the inference modules never name ground truth, and this module is
     excluded from that scan by name — so the exclusion is visible and deliberate
     rather than an accident of a loose glob.

If evaluation ever needs to run before or during inference, that is a redesign, not a
small edit. Ground truth entering the diagnosis path is the one defect this project
treats as unrecoverable.

WHAT REPLAY IS FOR
------------------
Re-running a recorded agent run against its RECORDED tool outputs, with no model call
and no database mutation, so that decision quality can be measured repeatedly and
cheaply. Replay answers "was this run well-grounded and well-bounded", not "does the
model still say the same words".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aventum_counterfactual.models import AgentRun, AgentToolCall, CounterfactualSimulation

from .constants import (
    MAX_SIMULATIONS,
    MAX_TOOL_CALLS,
    MAX_TURNS,
    OUTCOME_SUCCESS,
    QWEN_TURN_TIMEOUT_S,
    RUN_SUCCEEDED,
    TOTAL_AGENT_BUDGET_S,
)
from .loop import AgentOutcome

# Tools whose result the agent genuinely needed in order to proceed. A call outside
# this set on a run that already had what it needed counts as unnecessary.
_PROGRESS_TOOLS = frozenset({
    "get_incident_context", "get_detection_evidence", "get_gateway_health",
    "get_routing_options", "run_counterfactual", "estimate_business_impact",
    "check_action_bounds", "propose_action", "request_human_approval",
})


@dataclass
class AgentMetrics:
    """Quality metrics for one agent run. Every field is measured, none estimated."""

    agent_run_id: int | None
    status: str
    final_state: str

    turns_used: int = 0
    tool_calls_used: int = 0
    simulations_used: int = 0
    unique_tools_used: int = 0
    duplicate_tool_calls: int = 0
    failed_tool_calls: int = 0

    grounded_claims: int = 0
    total_claims: int = 0
    unsupported_claims: int = 0
    # Citations the loop refused before they could persist. A security metric:
    # non-zero here means the model tried to fabricate and the guard caught it.
    blocked_fabrication_attempts: int = 0

    policy_violations: int = 0
    recommendation_consistent: bool | None = None

    within_turn_budget: bool = True
    within_tool_budget: bool = True
    within_simulation_budget: bool = True
    within_time_budget: bool = True

    elapsed_ms: float = 0.0
    mean_qwen_latency_ms: float | None = None
    p95_qwen_latency_ms: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def grounded_claim_rate(self) -> float:
        """Share of cited IDs that resolve to something a tool actually returned."""
        return (self.grounded_claims / self.total_claims) if self.total_claims else 1.0

    @property
    def unsupported_claim_rate(self) -> float:
        """Target: 0.0. Any non-zero value is a grounding failure."""
        return (self.unsupported_claims / self.total_claims) if self.total_claims else 0.0

    @property
    def unnecessary_tool_call_rate(self) -> float:
        return (self.duplicate_tool_calls / self.tool_calls_used) if self.tool_calls_used else 0.0

    @property
    def tool_selection_efficiency(self) -> float:
        if not self.tool_calls_used:
            return 1.0
        return (self.tool_calls_used - self.duplicate_tool_calls) / self.tool_calls_used

    @property
    def budget_compliant(self) -> bool:
        return all([self.within_turn_budget, self.within_tool_budget,
                    self.within_simulation_budget, self.within_time_budget])

    def as_dict(self) -> dict:
        return {
            "agent_run_id": self.agent_run_id,
            "status": self.status,
            "final_state": self.final_state,
            "turns_used": self.turns_used,
            "tool_calls_used": self.tool_calls_used,
            "simulations_used": self.simulations_used,
            "unique_tools_used": self.unique_tools_used,
            "duplicate_tool_calls": self.duplicate_tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "grounded_claim_rate": round(self.grounded_claim_rate, 4),
            "unsupported_claim_rate": round(self.unsupported_claim_rate, 4),
            "unsupported_claims": self.unsupported_claims,
            "blocked_fabrication_attempts": self.blocked_fabrication_attempts,
            "policy_violations": self.policy_violations,
            "recommendation_consistent": self.recommendation_consistent,
            "tool_selection_efficiency": round(self.tool_selection_efficiency, 4),
            "unnecessary_tool_call_rate": round(self.unnecessary_tool_call_rate, 4),
            "budget_compliant": self.budget_compliant,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "mean_qwen_latency_ms": self.mean_qwen_latency_ms,
            "p95_qwen_latency_ms": self.p95_qwen_latency_ms,
            "notes": self.notes,
        }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[int(fraction * (len(ordered) - 1))], 1)


def measure_run(session: Session, outcome: AgentOutcome) -> AgentMetrics:
    """
    Score a completed run from its persisted record. No ground truth involved.

    Grounding is measured against the IDs tools actually exposed, which is why the loop
    tracks exposure: a citation is grounded if and only if some tool in this run
    returned that ID.
    """
    metrics = AgentMetrics(
        agent_run_id=outcome.agent_run_id, status=outcome.status,
        final_state=outcome.final_state, turns_used=len(outcome.turns),
        tool_calls_used=outcome.tool_calls_used,
        simulations_used=outcome.simulations_used, elapsed_ms=outcome.elapsed_ms,
    )

    if outcome.qwen_latencies_ms:
        metrics.mean_qwen_latency_ms = round(
            sum(outcome.qwen_latencies_ms) / len(outcome.qwen_latencies_ms), 1)
        metrics.p95_qwen_latency_ms = _percentile(outcome.qwen_latencies_ms, 0.95)

    metrics.within_turn_budget = metrics.turns_used <= MAX_TURNS
    metrics.within_tool_budget = metrics.tool_calls_used <= MAX_TOOL_CALLS
    metrics.within_simulation_budget = metrics.simulations_used <= MAX_SIMULATIONS
    # The wall-clock budget is checked BETWEEN turns, so a run stopped by the guard has
    # necessarily just passed the deadline — the turn that crossed it was already in
    # flight. Scoring that as non-compliant would mark correct enforcement as a failure.
    # The bound the design actually guarantees is "budget + at most one turn", so that
    # is what is measured. Anything beyond it means the guard did not fire.
    grace_ms = QWEN_TURN_TIMEOUT_S * 1000
    metrics.within_time_budget = (
        outcome.elapsed_ms <= TOTAL_AGENT_BUDGET_S * 1000 + grace_ms
    )

    if outcome.agent_run_id is not None:
        calls = session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.agent_run_id == outcome.agent_run_id)
            .order_by(AgentToolCall.sequence)
        ).all()
        metrics.unique_tools_used = len({c.tool_name for c in calls})
        metrics.failed_tool_calls = sum(1 for c in calls if c.outcome != OUTCOME_SUCCESS)

        seen: set[tuple] = set()
        for call in calls:
            key = (call.tool_name, str(sorted((call.request or {}).items())))
            if key in seen:
                metrics.duplicate_tool_calls += 1
            seen.add(key)

    # --- grounding: every cited ID must have been exposed by a tool in THIS run ---
    # Seeded from the context, then grown by tool exposure. Both are legitimate
    # sources of a citable ID; counting only tool output would mark a model for
    # citing the candidates the system handed it.
    exposed_ev: set[int] = set(outcome.seeded_evidence_ids)
    exposed_sim: set[int] = set(outcome.seeded_simulation_ids)
    for turn in outcome.turns:
        result = turn.tool_result or {}
        auth = result.get("authoritative") or {}
        if "evidence" in auth:
            exposed_ev.update(int(e["evidence_id"]) for e in auth["evidence"])
        for key in ("supporting_evidence_ids",):
            rca = (auth.get("rca") or {})
            if isinstance(rca, dict) and rca.get(key):
                exposed_ev.update(int(i) for i in rca[key])
        if auth.get("simulation_id") is not None:
            exposed_sim.add(int(auth["simulation_id"]))

        # A citation on a REJECTED turn never reached a persisted record — the loop
        # refused the turn. That is a BLOCKED FABRICATION ATTEMPT, which is a security
        # success, not a grounding failure. Counting it as an unsupported claim would
        # conflate "the model tried something" with "the system stored something wrong",
        # and would make the defence look like the defect it prevented.
        cited_ev = turn.decision.get("evidence_ids") or []
        cited_sim = turn.decision.get("simulation_ids") or []
        rejected = turn.rejection is not None

        for cid in cited_ev:
            grounded = cid in exposed_ev
            if rejected:
                if not grounded:
                    metrics.blocked_fabrication_attempts += 1
                    metrics.notes.append(f"BLOCKED: fabricated evidence_id {cid}")
                continue
            metrics.total_claims += 1
            if grounded:
                metrics.grounded_claims += 1
            else:
                metrics.unsupported_claims += 1
                metrics.notes.append(f"unsupported evidence_id {cid} reached a turn")
        for sid in cited_sim:
            grounded = sid in exposed_sim
            if rejected:
                if not grounded:
                    metrics.blocked_fabrication_attempts += 1
                    metrics.notes.append(f"BLOCKED: fabricated simulation_id {sid}")
                continue
            metrics.total_claims += 1
            if grounded:
                metrics.grounded_claims += 1
            else:
                metrics.unsupported_claims += 1
                metrics.notes.append(f"unsupported simulation_id {sid} reached a turn")

    # --- policy: a BLOCKED simulation must never have become a recommendation ---
    if outcome.recommendation_id is not None and outcome.selected_simulation_id is not None:
        sim = session.get(CounterfactualSimulation, outcome.selected_simulation_id)
        row = session.execute(
            text("SELECT policy_validation_result FROM recommendations "
                 "WHERE recommendation_id = :r"),
            {"r": outcome.recommendation_id},
        ).scalar()
        if row == "BLOCKED":
            metrics.policy_violations += 1
            metrics.notes.append("recommendation was BLOCKED yet the run proceeded")
        metrics.recommendation_consistent = bool(sim is not None and sim.status == "VALID")
        if not metrics.recommendation_consistent:
            metrics.notes.append("selected simulation is not a VALID persisted candidate")

    return metrics


@dataclass
class ReplayResult:
    """Outcome of replaying a recorded run against its recorded tool outputs."""

    agent_run_id: int
    recorded_tool_sequence: list[str]
    recorded_outcomes: list[str]
    tool_calls: int
    simulations: int
    status: str
    final_state: str
    matches_expected: bool | None = None
    divergences: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "agent_run_id": self.agent_run_id,
            "recorded_tool_sequence": self.recorded_tool_sequence,
            "recorded_outcomes": self.recorded_outcomes,
            "tool_calls": self.tool_calls,
            "simulations": self.simulations,
            "status": self.status,
            "final_state": self.final_state,
            "matches_expected": self.matches_expected,
            "divergences": self.divergences,
        }


def replay_run(
    session: Session,
    agent_run_id: int,
    expected_tools: list[str] | None = None,
    expected_status: str | None = None,
) -> ReplayResult:
    """
    Reconstruct a recorded run from `agent_runs` + `agent_tool_calls`.

    Calls no model and mutates nothing — it reads the persisted record, which is
    exactly the property that makes the audit trail worth keeping: the run can be
    examined later without re-running it or trusting a summary of it.
    """
    run = session.get(AgentRun, agent_run_id)
    if run is None:
        raise ValueError(f"no agent run {agent_run_id}")

    calls = session.scalars(
        select(AgentToolCall)
        .where(AgentToolCall.agent_run_id == agent_run_id)
        .order_by(AgentToolCall.sequence)
    ).all()

    sequence = [c.tool_name for c in calls]
    outcomes = [c.outcome for c in calls]
    result = ReplayResult(
        agent_run_id=agent_run_id, recorded_tool_sequence=sequence,
        recorded_outcomes=outcomes, tool_calls=len(calls),
        simulations=sum(1 for c in calls if c.tool_name == "run_counterfactual"),
        status=run.status, final_state=run.status,
    )

    divergences = []
    if expected_tools is not None:
        missing = [t for t in expected_tools if t not in sequence]
        if missing:
            divergences.append(f"expected tools never called: {missing}")
    if expected_status is not None and run.status != expected_status:
        divergences.append(f"status {run.status} != expected {expected_status}")

    result.divergences = divergences
    result.matches_expected = not divergences
    return result


# ---------------------------------------------------------------------------
# GROUND-TRUTH SCORING — evaluation only, on a COMPLETED run
# ---------------------------------------------------------------------------
def score_against_ground_truth(
    session: Session, incident_id: int, outcome: AgentOutcome
) -> dict:
    """
    Compare a FINISHED run's conclusion against the answer key.

    This is the only ground-truth read in the agent layer, and it happens strictly
    after the fact: `outcome` is already complete, already persisted, and cannot be
    changed by anything computed here. The return value is a scorecard for a human or
    a test, never an input to inference.

    Deliberately NOT importable from the inference path — see the module docstring.
    """
    row = session.execute(
        text("SELECT ground_truth_root_cause FROM incident_ground_truth "
             "WHERE incident_id = :i"),
        {"i": incident_id},
    ).scalar()

    return {
        "evaluation_only": True,
        "boundary_note": (
            "Ground truth was read AFTER the agent run completed, solely to score it. "
            "No inference module imports this function."
        ),
        "incident_id": incident_id,
        "ground_truth_root_cause": row,
        "agent_status": outcome.status,
        "agent_final_state": outcome.final_state,
        "agent_recommendation_id": outcome.recommendation_id,
        "agent_selected_simulation_id": outcome.selected_simulation_id,
    }
