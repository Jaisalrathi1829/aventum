"""
Wire shapes for the browser, and the provenance labels that travel with them.

WHY THE LABELS ARE ASSIGNED HERE
--------------------------------
§9 requires the UI to distinguish observed fact from synthetic infrastructure, modelled
projection, measured simulated outcome, deterministic verdict, agent prose and human
decision. If React decided which label a number carries, the truth model would live in
the layer least able to defend it and would drift the first time a component was reused.

So every payload names its own provenance. The frontend renders the label it is given
and has no vocabulary for inventing another.

`UNAVAILABLE` is a real value here, not a bug. Capacity telemetry does not exist
anywhere in this system, and §11 requires saying so rather than approximating.
"""

from __future__ import annotations

from datetime import datetime

# ------------------------------------------------------------------ truth vocabulary
OBSERVED = "OBSERVED"
SYNTHETIC = "SYNTHETIC"
SIMULATED = "SIMULATED"
PROJECTED = "PROJECTED"
VERIFIED = "VERIFIED"
DETERMINISTIC = "DETERMINISTIC"
AI_GENERATED = "AI_GENERATED"
HUMAN = "HUMAN"

UNAVAILABLE = "UNAVAILABLE"

ENVIRONMENT_NOTICE = {
    "mode": "SIMULATION MODE",
    "detail": "Synthetic infrastructure • Simulated execution • No live routing changes",
    "no_live_telemetry": True,
    "no_production_execution": True,
    "capacity": UNAVAILABLE,
}


def iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def num(value) -> float | None:
    """A float, or None. Never a substituted zero -- absent is not the same as nought."""
    return None if value is None else float(value)


def _sim_row(sim) -> dict:
    """One counterfactual candidate, with projection and identity kept distinct."""
    return {
        "simulation_id": sim.simulation_id,
        "candidate_key": sim.candidate_key,
        "action_type": sim.action_type,
        "source_gateway_id": sim.source_gateway_id,
        "target_gateway_id": sim.target_gateway_id,
        "traffic_percentage": num(sim.traffic_percentage),
        "status": sim.status,
        "invalid_reason": sim.invalid_reason,
        # Everything below is a PROJECTION produced before any action was taken.
        "projected": {
            "truth": PROJECTED,
            "baseline_success_rate": num(sim.baseline_success_rate),
            "projected_success_rate": num(sim.projected_success_rate),
            "expected_success_delta": num(sim.expected_success_delta),
            "projected_failure_count": sim.projected_failure_count,
            "projected_gmv_total": num(sim.projected_gmv_total),
            "projected_gmv_retained": num(sim.projected_gmv_retained),
            "projected_gmv_at_risk": num(sim.projected_gmv_at_risk),
            "projected_latency_p50": num(sim.projected_latency_p50),
            "projected_latency_p95": num(sim.projected_latency_p95),
            "latency_delta_ms": num(sim.latency_delta_ms),
            "concentration_after": num(sim.concentration_after),
            "risk_score": num(sim.risk_score),
        },
        "affected_population": sim.affected_population,
        "rerouted_population": sim.rerouted_population,
        "capacity_utilization": (
            UNAVAILABLE if sim.capacity_utilization is None else num(sim.capacity_utilization)
        ),
        "eligibility_result": sim.eligibility_result,
        "risk_components": sim.risk_components,
        # Provenance and reproducibility surface (§14).
        "identity": {
            "simulation_seed": sim.simulation_seed,
            "input_fingerprint": sim.input_fingerprint,
            "simulation_fingerprint": sim.simulation_fingerprint,
            "model_version": sim.model_version,
            "policy_version": sim.policy_version,
            "profile_version": sim.profile_version,
        },
        "held_constant": sim.held_constant,
        "changed_variables": sim.changed_variables,
        "assumptions": sim.assumptions,
        "limitations": sim.limitations,
        "is_simulated": sim.is_simulated,
        "truth": SIMULATED,
    }


def simulation_list(sims) -> list[dict]:
    return [_sim_row(s) for s in sims]


def recommendation_row(rec, decision: dict | None = None) -> dict:
    """
    The persisted recommendation.

    `policy` is carried as the DETERMINISTIC verdict and is never merged into the
    agent's rationale: §15 requires "AI proposes, policy decides" to survive as two
    separately attributable facts all the way to the screen.
    """
    return {
        "recommendation_id": rec.recommendation_id,
        "incident_id": rec.incident_id,
        "analysis_run_id": rec.analysis_run_id,
        "simulation_id": rec.simulation_id,
        "agent_run_id": rec.agent_run_id,
        "action_type": rec.action_type,
        "source_gateway_id": rec.source_gateway_id,
        "target_gateway_id": rec.target_gateway_id,
        "traffic_percentage": num(rec.traffic_percentage),
        "expected": {
            "truth": PROJECTED,
            "expected_success_delta": num(rec.expected_success_delta),
            "expected_gmv_retained": num(rec.expected_gmv_retained),
            "expected_latency_delta_ms": num(rec.expected_latency_delta_ms),
        },
        "risk": {
            "truth": DETERMINISTIC,
            "risk_score": num(rec.risk_score),
            "risk_components": rec.risk_components,
        },
        "diagnosis": {
            "truth": DETERMINISTIC,
            "confidence": num(rec.confidence),
            "evidence_strength": num(rec.evidence_strength),
            "significance_sigma": num(rec.significance_sigma),
            "severity": rec.severity,
        },
        "supporting_evidence_ids": list(rec.supporting_evidence_ids or []),
        "alternatives_considered": rec.alternatives_considered,
        # Agent prose, clearly attributed and never load-bearing for authorisation.
        "rationale": rec.rationale,
        "rationale_truth": AI_GENERATED if rec.rationale else None,
        "policy": {
            "truth": DETERMINISTIC,
            "validation_result": rec.policy_validation_result,
            "reason_codes": rec.policy_reason_codes,
            "constraints": rec.constraints,
            "policy_version": rec.policy_version,
            **({"decision": decision} if decision else {}),
        },
        "status": rec.status,
        "expires_at": iso(rec.expires_at),
        "recommendation_fingerprint": rec.recommendation_fingerprint,
        "model_version": rec.model_version,
        "created_at": iso(rec.created_at),
    }


def approval_row(approval) -> dict:
    """The persisted human decision. `truth: HUMAN` -- a person, not a system, did this."""
    return {
        "approval_id": approval.approval_id,
        "recommendation_id": approval.recommendation_id,
        "status": approval.status,
        "truth": HUMAN,
        "requested_at": iso(approval.requested_at),
        "decided_at": iso(approval.decided_at),
        "expires_at": iso(approval.expires_at),
        "approver_identity": approval.approver_identity,
        "decision_note": approval.decision_note,
        "approval_fingerprint": approval.approval_fingerprint,
        "payload": approval.payload,
    }


def action_row(action) -> dict:
    """
    The persisted action.

    `expected_outcome` and `actual_simulated_outcome` are returned as two separate
    objects with two different truth labels, because §10 forbids confusing what was
    predicted with what was measured -- and the only reliable way to stop a UI doing
    that is to never hand it a merged object.
    """
    return {
        "action_id": action.action_id,
        "recommendation_id": action.recommendation_id,
        "approval_id": action.approval_id,
        "adapter_name": action.adapter_name,
        "status": action.status,
        "rejection_reason": action.rejection_reason,
        "revalidation_result": action.revalidation_result,
        "pre_action_metrics": (
            None if action.pre_action_metrics is None
            else {"truth": SIMULATED, **action.pre_action_metrics}
        ),
        "expected_outcome": (
            None if action.expected_outcome is None
            else {"truth": PROJECTED, **action.expected_outcome}
        ),
        "actual_simulated_outcome": (
            None if action.actual_simulated_outcome is None
            else {"truth": SIMULATED, **action.actual_simulated_outcome}
        ),
        "cohort_definition": action.cohort_definition,
        "measurement_window": action.measurement_window,
        "execution_fingerprint": action.execution_fingerprint,
        "reference_simulation_fingerprint": action.reference_simulation_fingerprint,
        "rollback_of_action_id": action.rollback_of_action_id,
        "rollback_reason": action.rollback_reason,
        "executed_at": iso(action.executed_at),
        "executed_by": action.executed_by,
        "is_simulated": action.is_simulated,
        "created_at": iso(action.created_at),
    }


def verification_row(result) -> dict:
    """
    The independent verdict.

    Note the three-way split: what the action STARTED from (`baseline`), what the
    simulation PROJECTED, and what was MEASURED. Executed is not verified, and a UI
    reading this cannot accidentally render one as the other.
    """
    return {
        "verification_id": result.verification_id,
        "action_id": result.action_id,
        "status": result.status,
        "outcome": result.outcome,
        "ineligible_reason": result.ineligible_reason,
        "truth": VERIFIED,
        "baseline": {
            "truth": SIMULATED,
            "failure_rate": result.baseline_failure_rate,
            "success_rate": result.baseline_success_rate,
            "gmv_at_risk": result.baseline_gmv_at_risk,
        },
        "projected": {
            "truth": PROJECTED,
            "success_delta": result.projected_success_delta,
            "gmv_retained": result.projected_gmv_retained,
        },
        "actual_simulated": {
            "truth": SIMULATED,
            "failure_rate": result.actual_failure_rate,
            "success_rate": result.actual_success_rate,
            "gmv_at_risk": result.actual_gmv_at_risk,
        },
        "measured": {
            "truth": VERIFIED,
            "success_delta": result.measured_success_delta,
            "failure_rate_improvement": result.measured_failure_rate_improvement,
            "gmv_recovered": result.actual_gmv_recovered,
            "variance_vs_projection": result.variance_vs_projection,
            "attainment_ratio": result.attainment_ratio,
            "transactions_moved": result.transactions_moved,
            "population": result.population,
        },
        "integrity_passed": result.integrity_passed,
        "integrity_checks": result.integrity_checks,
        "reasons": result.reasons,
        "verification_fingerprint": result.verification_fingerprint,
    }


def audit_row(event) -> dict:
    return {
        "event_id": event.event_id,
        "incident_id": event.incident_id,
        "event_type": event.event_type,
        "actor": event.actor,
        "input_ref": event.input_ref,
        "output_ref": event.output_ref,
        "payload": event.payload,
        "model_version": event.model_version,
        "policy_version": event.policy_version,
        "tool_version": event.tool_version,
        "fingerprint": event.fingerprint,
        "occurred_at": iso(event.occurred_at),
    }


def agent_run_row(run, tool_calls: list | None = None) -> dict:
    """
    Agent activity for the operator panel.

    Deliberately excludes anything resembling chain-of-thought (§21). What is exposed is
    what the agent DID -- tools called, status, budget consumed -- never a trace of how
    it reached a conclusion. With `think:false` in Day 4B no such trace is even produced.
    """
    return {
        "agent_run_id": run.agent_run_id,
        "incident_id": run.incident_id,
        "analysis_run_id": run.analysis_run_id,
        "status": run.status,
        "truth": AI_GENERATED,
        "model_name": run.model_name,
        "turns_used": run.turns_used,
        "tool_calls_used": run.tool_calls_used,
        "simulations_used": run.simulations_used,
        "context_tokens_max": run.context_tokens_max,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
        "error_message": run.error_message,
        "tool_calls": tool_calls or [],
    }


def tool_call_row(call) -> dict:
    """
    One tool invocation.

    `request` and `response` are omitted: the response of a ground-truth-adjacent tool
    is not something the browser needs, and the panel's job is to show WHAT the agent
    did, not to replay its inputs.
    """
    return {
        "tool_call_id": call.tool_call_id,
        "sequence": call.sequence,
        "tool_name": call.tool_name,
        "outcome": call.outcome,
        "attempt": call.attempt,
        "latency_ms": num(call.latency_ms),
        "created_at": iso(call.created_at),
    }
