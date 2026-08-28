"""
Simulated execution, with full revalidation.

EXECUTION DOES NOT TRUST WHAT IT IS HANDED
-------------------------------------------
`execute_action()` takes IDs, not objects. Everything it checks is re-read from the
database inside the executing transaction, and the two most important checks are
re-DERIVED rather than read at all:

  * the input fingerprint is recomputed from the CURRENT world and compared;
  * the FULL policy gate is re-run against current data.

Both are derived checks, which is what makes a stale action impossible to execute. A
status column saying "still valid" can be edited; a hash over the actual inputs and a
freshly-run gate cannot. If the incident window moved, a health state changed, a profile
was re-versioned, or the cohort gained a transaction, the fingerprint simply differs and
the action is REJECTED.

THIRTEEN CHECKS, ANY FAILURE IS TERMINAL
-----------------------------------------
No partial execution, no override path, no force flag. Every rejection is persisted on
the action row with a machine-readable reason and emitted as an audit event, so a
refusal is as reconstructable as a success.

THE ACTION ROW IS INSERTED BEFORE THE ADAPTER RUNS
---------------------------------------------------
That ordering is the concurrency guarantee. The INSERT carries the UNIQUE
`idempotency_key`, so two concurrent callers contend at the database: one wins, the other
blocks until the winner commits and then fails the unique constraint. The loser is
deflected to the winner's stored result and an ACTION_DUPLICATE_SUPPRESSED event is
written. The adapter is therefore invoked exactly once, by PostgreSQL's serialisation
rather than by application-level timing that a race could slip past.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aventum_counterfactual.constants import ACTION_NO_ACTION, STATUS_VALID
from aventum_counterfactual.fingerprint import (
    compute_idempotency_key,
    compute_input_fingerprint,
)
from aventum_counterfactual.models import CounterfactualSimulation
from aventum_counterfactual.simulator import Candidate, _project_outcomes, affected_cohort
from aventum_counterfactual.source import WorldState, load_rca
from aventum_policy import POLICY_VERSION
from aventum_policy.constants import (
    APPROVAL_EXPIRED,
    APPROVAL_FINGERPRINT_MISMATCH,
    APPROVAL_NOT_APPROVED,
    APPROVAL_NOT_FOUND,
    DUPLICATE_EXECUTION,
    POLICY_REVALIDATION_FAILED,
    POLICY_VERSION_CHANGED,
    RECOMMENDATION_EXPIRED,
    RECOMMENDATION_NOT_APPROVED,
    RECOMMENDATION_NOT_FOUND,
    SIMULATION_INVALID,
    STALE_SIMULATION,
    TARGET_NOT_ELIGIBLE,
    TARGET_NOT_HEALTHY,
)
from aventum_policy.gate import validate

from .adapter import ActionRequest, RoutingActionAdapter, SimulatedRoutingAdapter
from .audit import (
    ACTION_DUPLICATE_SUPPRESSED,
    ACTION_EXECUTED,
    ACTION_REJECTED,
    ACTION_ROLLED_BACK,
    ACTOR_SYSTEM,
    emit,
    human_actor,
    ref,
)
from .models import Action, Approval, Recommendation
from .recommendation import advance_status

STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_PENDING = "PENDING"
STATUS_ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of an execution attempt, successful, rejected, or deflected."""

    action: Action
    executed: bool
    duplicate: bool = False
    rejection_reason: str | None = None

    @property
    def result(self) -> dict | None:
        return self.action.actual_simulated_outcome


class _Check:
    """One revalidation step's verdict."""

    __slots__ = ("name", "passed", "code", "detail")

    def __init__(self, name: str, passed: bool, code: str | None, detail: object = None) -> None:
        self.name, self.passed, self.code, self.detail = name, passed, code, detail

    def as_dict(self) -> dict:
        return {
            "check": self.name,
            "result": "PASS" if self.passed else "FAIL",
            "reason_code": self.code,
            "detail": self.detail,
        }


def execute_action(
    session: Session,
    *,
    recommendation_id: int,
    approval_id: int,
    world: WorldState,
    alert_role: str | None,
    adapter: RoutingActionAdapter | None = None,
    executed_by: str = "operator",
    now: datetime | None = None,
) -> ExecutionOutcome:
    """
    Revalidate everything, then execute exactly once through a simulated adapter.

    Takes IDs rather than objects on purpose: a caller cannot hand in a mutated
    recommendation or a hand-built approval, because neither is accepted as input.
    """
    now = now or datetime.now(timezone.utc)
    adapter = adapter or SimulatedRoutingAdapter()
    idempotency_key = compute_idempotency_key(recommendation_id, approval_id, adapter.name)

    # ---- fast path: this exact action already ran -----------------------------------
    existing = session.scalar(select(Action).where(Action.idempotency_key == idempotency_key))
    if existing is not None:
        return _suppress_duplicate(session, existing, now)

    # ---- claim the idempotency key BEFORE doing anything -----------------------------
    # Inserting first is what makes concurrency safe: the UNIQUE constraint decides the
    # winner, not the order in which two callers happen to reach the adapter.
    action = Action(
        recommendation_id=recommendation_id,
        approval_id=approval_id,
        idempotency_key=idempotency_key,
        adapter_name=adapter.name,
        status=STATUS_PENDING,
    )
    session.add(action)
    try:
        session.flush()
    except IntegrityError:
        # A concurrent caller won the race. Deflect to their result.
        session.rollback()
        winner = session.scalar(select(Action).where(Action.idempotency_key == idempotency_key))
        if winner is None:  # pragma: no cover - only if the row vanished
            raise
        return _suppress_duplicate(session, winner, now)

    checks: list[_Check] = []

    def reject(code: str) -> ExecutionOutcome:
        action.status = STATUS_REJECTED
        action.rejection_reason = code
        action.revalidation_result = {
            "checks": [c.as_dict() for c in checks],
            "rejected_with": code,
            "revalidated_at": now.isoformat(),
            "note": "No partial execution occurred. The adapter was never invoked.",
        }
        session.flush()
        emit(
            session,
            event_type=ACTION_REJECTED,
            actor=ACTOR_SYSTEM,
            incident_id=world.incident_id,
            input_ref=ref("recommendations", recommendation_id),
            output_ref=ref("actions", action.action_id),
            payload={"reason_code": code, "checks": [c.as_dict() for c in checks]},
        )
        return ExecutionOutcome(action=action, executed=False, rejection_reason=code)

    # ---- 1. recommendation exists ----------------------------------------------------
    recommendation = session.get(Recommendation, recommendation_id)
    checks.append(_Check("recommendation_exists", recommendation is not None,
                         None if recommendation else RECOMMENDATION_NOT_FOUND))
    if recommendation is None:
        return reject(RECOMMENDATION_NOT_FOUND)

    # ---- 2. recommendation is APPROVED ----------------------------------------------
    ok = recommendation.status == "APPROVED"
    checks.append(_Check("recommendation_approved", ok,
                         None if ok else RECOMMENDATION_NOT_APPROVED, recommendation.status))
    if not ok:
        return reject(RECOMMENDATION_NOT_APPROVED)

    # ---- 3. approval exists ----------------------------------------------------------
    approval = session.get(Approval, approval_id)
    ok = approval is not None and approval.recommendation_id == recommendation_id
    checks.append(_Check("approval_exists", ok, None if ok else APPROVAL_NOT_FOUND))
    if not ok:
        return reject(APPROVAL_NOT_FOUND)

    # ---- 4. approval is APPROVED -----------------------------------------------------
    ok = approval.status == "APPROVED"
    checks.append(_Check("approval_approved", ok,
                         None if ok else APPROVAL_NOT_APPROVED, approval.status))
    if not ok:
        return reject(APPROVAL_NOT_APPROVED)

    # ---- 5. approval not expired -----------------------------------------------------
    ok = now <= approval.expires_at
    checks.append(_Check("approval_not_expired", ok, None if ok else APPROVAL_EXPIRED,
                         approval.expires_at.isoformat()))
    if not ok:
        return reject(APPROVAL_EXPIRED)

    # ---- 6. recommendation not expired -----------------------------------------------
    ok = now <= recommendation.expires_at
    checks.append(_Check("recommendation_not_expired", ok,
                         None if ok else RECOMMENDATION_EXPIRED,
                         recommendation.expires_at.isoformat()))
    if not ok:
        return reject(RECOMMENDATION_EXPIRED)

    # ---- 7. approval fingerprint still matches the recommendation content -------------
    # Catches a recommendation edited after a human approved it: the approval was for
    # different content, so it is not transferable to this one.
    ok = approval.approval_fingerprint == recommendation.recommendation_fingerprint
    checks.append(_Check("approval_fingerprint_matches", ok,
                         None if ok else APPROVAL_FINGERPRINT_MISMATCH))
    if not ok:
        return reject(APPROVAL_FINGERPRINT_MISMATCH)

    simulation = session.get(CounterfactualSimulation, recommendation.simulation_id)
    ok = simulation is not None and simulation.status == STATUS_VALID
    checks.append(_Check("simulation_valid", ok, None if ok else SIMULATION_INVALID))
    if not ok:
        return reject(SIMULATION_INVALID)

    # ---- 8. input fingerprint RE-DERIVED from the current world -----------------------
    current_fingerprint = compute_input_fingerprint(
        world, simulation.simulation_seed, simulation.policy_version
    )
    ok = current_fingerprint == simulation.input_fingerprint
    checks.append(_Check("simulation_fresh", ok, None if ok else STALE_SIMULATION,
                         {"expected": simulation.input_fingerprint[:16] + "...",
                          "current": current_fingerprint[:16] + "..."}))
    if not ok:
        return reject(STALE_SIMULATION)

    # ---- 9. policy version unchanged since validation ---------------------------------
    ok = recommendation.policy_version == POLICY_VERSION
    checks.append(_Check("policy_version_unchanged", ok,
                         None if ok else POLICY_VERSION_CHANGED,
                         {"validated_under": recommendation.policy_version,
                          "current": POLICY_VERSION}))
    if not ok:
        return reject(POLICY_VERSION_CHANGED)

    # ---- 10. FULL policy gate re-run against current data -----------------------------
    decision = validate(simulation, load_rca(session, recommendation.analysis_run_id),
                        world, alert_role, now=now)
    checks.append(_Check("policy_revalidated", decision.permitted,
                         None if decision.permitted else POLICY_REVALIDATION_FAILED,
                         decision.reason_codes))
    if not decision.permitted:
        return reject(POLICY_REVALIDATION_FAILED)

    # ---- 11 & 12. target health and eligibility, re-read explicitly -------------------
    # Redundant with the gate above by design: these two are the conditions most likely
    # to change between approval and execution, and an explicit named check makes the
    # rejection reason precise rather than a generic revalidation failure.
    if recommendation.action_type != ACTION_NO_ACTION:
        target = recommendation.target_gateway_id
        healthy, health_reason = world.healthy_for_whole_window(target)
        checks.append(_Check("target_healthy", healthy,
                             None if healthy else TARGET_NOT_HEALTHY, health_reason))
        if not healthy:
            return reject(TARGET_NOT_HEALTHY)

        eligibility = world.eligibility.get(target)
        ok = bool(eligibility and eligibility.is_eligible)
        checks.append(_Check("target_eligible", ok, None if ok else TARGET_NOT_ELIGIBLE))
        if not ok:
            return reject(TARGET_NOT_ELIGIBLE)

    # ---- 13. idempotency: already guaranteed by the claimed row above -----------------
    checks.append(_Check("idempotency_key_claimed", True, None, idempotency_key[:16] + "..."))

    # ---- all checks passed: replay the projection and invoke the adapter --------------
    cohort = affected_cohort(world)
    candidate = Candidate(
        action_type=simulation.action_type,
        target_gateway_id=simulation.target_gateway_id,
        traffic_percentage=float(simulation.traffic_percentage or 0),
        source_gateway_id=simulation.source_gateway_id,
    )
    from aventum_counterfactual.simulator import select_rerouted

    rerouted = select_rerouted(cohort, world, float(simulation.traffic_percentage or 0))
    outcomes = _project_outcomes(world, candidate, cohort, rerouted)

    pre_action_metrics = _pre_action_metrics(outcomes, simulation)
    request = ActionRequest(
        recommendation_id=recommendation_id,
        approval_id=approval_id,
        incident_id=world.incident_id,
        simulation_id=simulation.simulation_id,
        action_type=simulation.action_type,
        source_gateway_id=simulation.source_gateway_id,
        target_gateway_id=simulation.target_gateway_id,
        traffic_percentage=float(simulation.traffic_percentage or 0),
        projected_outcomes=outcomes,
        cohort_definition={
            "affected_gateway_id": world.affected_gateway_id,
            "affected_segment": world.affected_segment,
            "population": len(cohort),
        },
        measurement_window={
            "start": world.window_start.isoformat(),
            "end": world.window_end.isoformat(),
        },
        current_distribution=simulation.current_distribution or {},
    )
    result = adapter.apply(request)

    action.status = STATUS_EXECUTED
    action.executed_at = result.executed_at
    action.executed_by = executed_by
    action.revalidation_result = {
        "checks": [c.as_dict() for c in checks],
        "revalidated_at": now.isoformat(),
        "policy_result": decision.as_dict(),
    }
    action.pre_action_metrics = pre_action_metrics
    # Kept strictly apart. Day 5's entire job is the gap between these two.
    action.expected_outcome = _expected_outcome(simulation)
    action.actual_simulated_outcome = result.as_dict()
    action.cohort_definition = request.cohort_definition
    action.measurement_window = request.measurement_window
    action.execution_fingerprint = result.execution_fingerprint
    action.reference_simulation_fingerprint = simulation.simulation_fingerprint
    session.flush()

    advance_status(recommendation, "EXECUTED")
    session.flush()

    emit(
        session,
        event_type=ACTION_EXECUTED,
        actor=human_actor(executed_by),
        incident_id=world.incident_id,
        input_ref=ref("approvals", approval_id),
        output_ref=ref("actions", action.action_id),
        payload={
            "adapter": adapter.name,
            "traffic_moved": result.traffic_moved,
            "post_action_success_rate": round(result.post_action_success_rate, 6),
            "provenance": "SIMULATED_EXECUTION",
            "recovery_claim": "NONE — Day 5 owns verification",
        },
        fingerprint=result.execution_fingerprint,
    )
    return ExecutionOutcome(action=action, executed=True)


def _suppress_duplicate(session: Session, action: Action, now: datetime) -> ExecutionOutcome:
    """Return the original result and record that a duplicate was deflected."""
    recommendation = session.get(Recommendation, action.recommendation_id)
    emit(
        session,
        event_type=ACTION_DUPLICATE_SUPPRESSED,
        actor=ACTOR_SYSTEM,
        incident_id=recommendation.incident_id if recommendation else None,
        input_ref=ref("actions", action.action_id),
        payload={
            "idempotency_key": action.idempotency_key,
            "original_status": action.status,
            "suppressed_at": now.isoformat(),
            "note": "The adapter was NOT invoked again; the original result is returned.",
        },
    )
    return ExecutionOutcome(
        action=action,
        executed=action.status == STATUS_EXECUTED,
        duplicate=True,
        rejection_reason=DUPLICATE_EXECUTION,
    )


def _pre_action_metrics(outcomes: list, simulation: CounterfactualSimulation) -> dict:
    """
    The baseline snapshot, taken at EXECUTION time rather than at simulation time.

    Day 5 compares against the world as it was when the action actually happened, which
    is not necessarily the world as it was when the candidate was first simulated.
    """
    total = len(outcomes)
    failures = sum(1 for o in outcomes if o.current_status == "FAILED")
    latencies = sorted(o.current_latency_ms for o in outcomes)
    return {
        "population": total,
        "success_rate": ((total - failures) / total) if total else 0.0,
        "failure_rate": (failures / total) if total else 0.0,
        "failure_count": failures,
        "gmv_total": round(sum(o.amount for o in outcomes), 2),
        "gmv_at_risk": round(
            sum(o.amount for o in outcomes if o.current_status == "FAILED"), 2
        ),
        "latency_p50": latencies[int(0.50 * (len(latencies) - 1))] if latencies else 0.0,
        "latency_p95": latencies[int(0.95 * (len(latencies) - 1))] if latencies else 0.0,
        "current_distribution": simulation.current_distribution,
        "measured_at": "EXECUTION_TIME",
        "basis": "OBSERVED_AMOUNTS + MODELLED_OUTCOMES",
    }


def _expected_outcome(simulation: CounterfactualSimulation) -> dict:
    """What the simulation PROJECTED. Never merged with what the adapter modelled."""
    return {
        "simulation_id": simulation.simulation_id,
        "projected_success_rate": float(simulation.projected_success_rate or 0),
        "expected_success_delta": float(simulation.expected_success_delta or 0),
        "projected_failure_count": simulation.projected_failure_count,
        "projected_gmv_retained": float(simulation.projected_gmv_retained or 0),
        "projected_gmv_at_risk": float(simulation.projected_gmv_at_risk or 0),
        "projected_latency_p50": float(simulation.projected_latency_p50 or 0),
        "projected_latency_p95": float(simulation.projected_latency_p95 or 0),
        "concentration_after": float(simulation.concentration_after or 0),
        "simulation_fingerprint": simulation.simulation_fingerprint,
        "source": "COUNTERFACTUAL_SIMULATION — projected before the action",
    }


def rollback(
    session: Session,
    action: Action,
    *,
    reason: str,
    executed_by: str = "operator",
    adapter: RoutingActionAdapter | None = None,
    now: datetime | None = None,
) -> Action:
    """
    Restore the prior allocation. A FORWARD transition, never a deletion.

    The original action row is never removed, so "we acted, then reverted" stays
    reconstructable. Day 5 owns the decision to invoke this; Day 4 only defines it.
    Idempotent: rolling back an already-rolled-back action is a no-op.
    """
    now = now or datetime.now(timezone.utc)
    adapter = adapter or SimulatedRoutingAdapter()

    if action.status == STATUS_ROLLED_BACK:
        return action
    if action.status != STATUS_EXECUTED:
        raise RuntimeError(
            f"action {action.action_id} is {action.status}; only an EXECUTED action can roll back"
        )

    action.status = STATUS_ROLLED_BACK
    action.rollback_reason = reason
    session.flush()

    recommendation = session.get(Recommendation, action.recommendation_id)
    emit(
        session,
        event_type=ACTION_ROLLED_BACK,
        actor=human_actor(executed_by),
        incident_id=recommendation.incident_id if recommendation else None,
        input_ref=ref("actions", action.action_id),
        payload={
            "reason": reason,
            "restored_allocation": (action.pre_action_metrics or {}).get("current_distribution"),
            "adapter": adapter.name,
            "rolled_back_at": now.isoformat(),
        },
    )
    return action
