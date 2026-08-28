"""
The Day 4A deterministic spine, end to end.

    Day 3 incident
      → counterfactual sweep (NO_ACTION first)
      → business impact
      → deterministic selection
      → policy gate
      → recommendation
      → [human approval]
      → simulated execution
      → audit

`run_decision_pipeline()` stops at the recommendation, deliberately. Approval is a HUMAN
step, so the pipeline hands back a decision and waits; it does not approve on the
operator's behalf, and there is no flag that makes it do so. `run_full_flow()` exists for
tests and demos and takes an explicit `approver_identity`, which is how a human's
participation is represented when a human is not physically present.

WHAT RUNNING THIS WITHOUT QWEN PROVES
--------------------------------------
Everything below executes with no LLM, no Ollama, no tool registry, and no agent loop.
The recommendations it produces carry `rationale = NULL`. That is the Day 4A thesis: the
deterministic spine is complete on its own, and the agent Day 4B adds is an explanation
layer over a system that already decides correctly without it -- not a dependency the
decisions rest on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from aventum_counterfactual.constants import ACTION_NO_ACTION
from aventum_counterfactual.models import CounterfactualSimulation
from aventum_counterfactual.optimize import SweepResult, run_candidate_sweep
from aventum_counterfactual.source import (
    WorldState,
    load_primary_anomalies,
    load_world_state,
)
from aventum_policy.gate import PolicyDecision

from .approval import decide_approval, request_approval
from .audit import ACTOR_SYSTEM, SIMULATION_COMPLETED, SIMULATION_INVALID, emit, ref
from .execute import ExecutionOutcome, execute_action
from .models import Action, Approval, Recommendation
from .recommendation import RecommendationResult, build_recommendation


@dataclass
class DecisionResult:
    """Everything the deterministic spine produced, up to the approval boundary."""

    world: WorldState
    sweep: SweepResult
    recommendation: Recommendation
    decision: PolicyDecision
    alert_role: str | None
    requires_approval: bool
    elapsed_ms: float = 0.0
    timings: dict = field(default_factory=dict)

    @property
    def chose_no_action(self) -> bool:
        return self.recommendation.action_type == ACTION_NO_ACTION


def primary_alert_role(session: Session, analysis_run_id: int) -> str | None:
    """
    The alert role Day 4 is allowed to act on.

    Returns PRIMARY only when a PRIMARY alert actually exists. Day 3's P1-1 fix
    established that a DERIVATIVE alert is a causal shadow of a stronger cohort; if the
    run produced no PRIMARY alert at all, this returns None and the policy gate blocks
    on ALERT_NOT_PRIMARY rather than quietly promoting a derivative one.
    """
    primaries = load_primary_anomalies(session, analysis_run_id)
    return "PRIMARY" if primaries else None


def run_decision_pipeline(
    session: Session,
    incident_id: int,
    analysis_run_id: int,
) -> DecisionResult:
    """
    Simulate every candidate, select deterministically, and build a recommendation.

    STOPS BEFORE APPROVAL. A human decides next; nothing here can decide for them.
    """
    started = time.perf_counter()
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    # Read the world ONCE, so every candidate is evaluated against an identical world.
    world = load_world_state(session, incident_id)
    timings["load_world_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)

    t0 = time.perf_counter()
    sweep = run_candidate_sweep(session, world, analysis_run_id)
    timings["candidate_sweep_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)

    for sim in [sweep.no_action] + sweep.candidates:
        emit(
            session,
            event_type=(
                SIMULATION_COMPLETED if sim.status == "VALID" else SIMULATION_INVALID
            ),
            actor=ACTOR_SYSTEM,
            incident_id=incident_id,
            output_ref=ref("counterfactual_simulations", sim.simulation_id),
            payload={
                "candidate_key": sim.candidate_key,
                "status": sim.status,
                "invalid_reason": sim.invalid_reason,
                "expected_gmv_retained": float(sim.projected_gmv_retained or 0),
                "expected_success_delta": float(sim.expected_success_delta or 0),
            },
            fingerprint=sim.simulation_fingerprint,
        )

    alert_role = primary_alert_role(session, analysis_run_id)

    t0 = time.perf_counter()
    result: RecommendationResult = build_recommendation(
        session,
        simulation_id=sweep.best.simulation_id,
        analysis_run_id=analysis_run_id,
        world=world,
        alert_role=alert_role,
        # rationale stays NULL: Day 4A has no agent and needs none.
        rationale=None,
        alternatives=sweep.alternatives(),
    )
    timings["recommendation_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)

    return DecisionResult(
        world=world,
        sweep=sweep,
        recommendation=result.recommendation,
        decision=result.decision,
        alert_role=alert_role,
        requires_approval=result.recommendation.action_type != ACTION_NO_ACTION,
        elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
        timings=timings,
    )


@dataclass
class FullFlowResult:
    decision: DecisionResult
    approval: Approval | None
    execution: ExecutionOutcome | None
    action: Action | None


def run_full_flow(
    session: Session,
    incident_id: int,
    analysis_run_id: int,
    *,
    approver_identity: str,
    approve: bool = True,
    executed_by: str | None = None,
) -> FullFlowResult:
    """
    The complete spine including a human decision, for tests and demos.

    `approver_identity` is REQUIRED and has no default -- an approval must always be
    attributed to a person, and making the caller name one keeps that true even in a
    scripted run. A NO_ACTION outcome returns early with no approval and no action,
    because it changes nothing and needs neither.
    """
    decision = run_decision_pipeline(session, incident_id, analysis_run_id)

    if not decision.requires_approval or not decision.decision.permitted:
        # Either NO_ACTION won (a successful terminal state) or the policy blocked the
        # intervention. Both stop here, correctly.
        return FullFlowResult(decision=decision, approval=None, execution=None, action=None)

    approval = request_approval(session, decision.recommendation, decision.decision)
    decide_approval(
        session,
        approval,
        decision="APPROVED" if approve else "REJECTED",
        approver_identity=approver_identity,
        note="Day 4A deterministic flow" if approve else "declined",
    )
    if not approve:
        return FullFlowResult(
            decision=decision, approval=approval, execution=None, action=None
        )

    execution = execute_action(
        session,
        recommendation_id=decision.recommendation.recommendation_id,
        approval_id=approval.approval_id,
        world=decision.world,
        alert_role=decision.alert_role,
        executed_by=executed_by or approver_identity,
    )
    return FullFlowResult(
        decision=decision,
        approval=approval,
        execution=execution,
        action=execution.action,
    )


def simulation_summary(sim: CounterfactualSimulation) -> dict:
    """Compact, print-friendly view of one candidate."""
    return {
        "simulation_id": sim.simulation_id,
        "candidate": sim.candidate_key,
        "status": sim.status,
        "invalid_reason": sim.invalid_reason,
        "expected_gmv_retained": float(sim.projected_gmv_retained or 0),
        "expected_success_delta": float(sim.expected_success_delta or 0),
        "projected_success_rate": float(sim.projected_success_rate or 0),
        "concentration_after": float(sim.concentration_after or 0),
        "latency_delta_ms": float(sim.latency_delta_ms or 0),
        "risk_score": float(sim.risk_score or 0),
        "rerouted_population": sim.rerouted_population,
    }
