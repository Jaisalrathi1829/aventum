"""
The Day 5 interface.

Day 5 (post-action verification) consumes Day 4 through this function and nothing else,
exactly as Day 4 consumes Day 3 through `aventum_incident.handoff`. Reconstructing the
picture by querying `actions` directly would couple Day 5 to Day 4's schema and let it
assemble a different version of the same history.

WHAT DAY 5 MUST BE ABLE TO DO, AND WHAT THIS GUARANTEES
--------------------------------------------------------
Day 5 has to answer "did it help?", which requires comparing a post-action measurement
against a pre-action baseline ON THE SAME COHORT, over the SAME window, with the SAME
metric definitions. So this handoff carries `cohort_definition` and `measurement_window`
alongside the numbers -- a comparison against a differently-measured "after" would make
any improvement claim meaningless.

`expected_outcome` and `actual_simulated_outcome` are returned as SEPARATE keys and are
never merged. The gap between them is the entire subject of Day 5.

WHAT THIS HANDOFF DELIBERATELY DOES NOT SAY
--------------------------------------------
It never states that recovery occurred. "Numbers changed" is not "recovery succeeded":
establishing improvement, on the same cohort, with the control group still available for
comparison -- and being able to conclude the action did NOT help -- is Day 5's job. Day
4A hands over the evidence and stops.

Ground truth is absent here, as everywhere in Day 4.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from aventum_counterfactual.constants import CAPACITY_UNAVAILABLE
from aventum_counterfactual.models import CounterfactualSimulation

from .models import Action, Approval, AuditEvent, Recommendation


@dataclass
class VerificationHandoff:
    """Everything Day 5 needs to verify one action, in one object."""

    action_id: int
    recommendation_id: int
    approval_id: int
    incident_id: int
    analysis_run_id: int
    simulation_id: int

    action_status: str
    action_type: str
    source_gateway_id: str | None
    target_gateway_id: str | None
    traffic_percentage: float

    # The baseline, snapshotted at execution time.
    pre_action_metrics: dict | None
    # What the simulation projected...
    expected_outcome: dict | None
    # ...and what the adapter modelled. Never merged.
    actual_simulated_outcome: dict | None

    cohort_definition: dict | None
    measurement_window: dict | None

    approver_identity: str | None
    executed_by: str | None
    executed_at: str | None
    execution_fingerprint: str | None
    reference_simulation_fingerprint: str | None
    input_fingerprint: str | None

    audit_event_ids: list[int] = field(default_factory=list)
    rollback_reference: dict | None = None
    provenance: str = "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION"
    capacity: str = CAPACITY_UNAVAILABLE
    verification_note: str = (
        "Day 4A makes NO recovery claim. Establishing whether the action helped — on "
        "this cohort, over this window, against the control group, with the option of "
        "concluding it did not — is Day 5's responsibility."
    )

    def as_dict(self) -> dict:
        return asdict(self)


def build_verification_handoff(session: Session, action_id: int) -> VerificationHandoff:
    """Assemble the complete Day 5 handoff for one executed (or rejected) action."""
    action = session.get(Action, action_id)
    if action is None:
        raise ValueError(f"no action {action_id}")

    recommendation = session.get(Recommendation, action.recommendation_id)
    approval = session.get(Approval, action.approval_id)
    simulation = (
        session.get(CounterfactualSimulation, recommendation.simulation_id)
        if recommendation
        else None
    )

    # Every audit event for this incident, in order -- the full reconstructable history
    # from detection through execution.
    audit_ids = list(
        session.scalars(
            select(AuditEvent.event_id)
            .where(AuditEvent.incident_id == (recommendation.incident_id if recommendation else None))
            .order_by(AuditEvent.event_id)
        ).all()
    )

    rollback_reference = None
    if action.status == "ROLLED_BACK" or action.rollback_reason:
        rollback_reference = {
            "rolled_back": action.status == "ROLLED_BACK",
            "reason": action.rollback_reason,
            "rollback_of_action_id": action.rollback_of_action_id,
            "restores": (action.pre_action_metrics or {}).get("current_distribution"),
        }

    return VerificationHandoff(
        action_id=action.action_id,
        recommendation_id=action.recommendation_id,
        approval_id=action.approval_id,
        incident_id=recommendation.incident_id if recommendation else 0,
        analysis_run_id=recommendation.analysis_run_id if recommendation else 0,
        simulation_id=recommendation.simulation_id if recommendation else 0,
        action_status=action.status,
        action_type=recommendation.action_type if recommendation else "",
        source_gateway_id=recommendation.source_gateway_id if recommendation else None,
        target_gateway_id=recommendation.target_gateway_id if recommendation else None,
        traffic_percentage=float(recommendation.traffic_percentage or 0) if recommendation else 0.0,
        pre_action_metrics=action.pre_action_metrics,
        expected_outcome=action.expected_outcome,
        actual_simulated_outcome=action.actual_simulated_outcome,
        cohort_definition=action.cohort_definition,
        measurement_window=action.measurement_window,
        approver_identity=approval.approver_identity if approval else None,
        executed_by=action.executed_by,
        executed_at=action.executed_at.isoformat() if action.executed_at else None,
        execution_fingerprint=action.execution_fingerprint,
        reference_simulation_fingerprint=action.reference_simulation_fingerprint,
        input_fingerprint=simulation.input_fingerprint if simulation else None,
        audit_event_ids=audit_ids,
        rollback_reference=rollback_reference,
    )


def provenance_chain(session: Session, action_id: int) -> dict:
    """
    The complete lineage of one action, by ID traversal.

        action → approval → recommendation → simulation → analysis run → incident
               → generation run → source ingestion run → dataset registry → source SHA-256

    Ground truth appears nowhere in this chain, by construction.
    """
    action = session.get(Action, action_id)
    if action is None:
        raise ValueError(f"no action {action_id}")
    recommendation = session.get(Recommendation, action.recommendation_id)
    simulation = session.get(CounterfactualSimulation, recommendation.simulation_id)

    from sqlalchemy import text

    lineage = session.execute(
        text(
            """
            SELECT i.incident_id, i.incident_key, i.generation_run_id,
                   i.source_ingestion_run_id,
                   g.generation_fingerprint, g.routing_policy_version,
                   r.source_sha256, r.canonical_fingerprint, r.source_dataset
            FROM incidents i
            JOIN synthetic_generation_runs g ON g.generation_run_id = i.generation_run_id
            JOIN ingestion_runs r ON r.ingestion_run_id = i.source_ingestion_run_id
            WHERE i.incident_id = :incident_id
            """
        ),
        {"incident_id": recommendation.incident_id},
    ).mappings().first()

    return {
        "action_id": action.action_id,
        "approval_id": action.approval_id,
        "recommendation_id": action.recommendation_id,
        "simulation_id": simulation.simulation_id,
        "simulation_input_fingerprint": simulation.input_fingerprint,
        "simulation_fingerprint": simulation.simulation_fingerprint,
        "analysis_run_id": recommendation.analysis_run_id,
        "incident_id": recommendation.incident_id,
        "incident_key": lineage["incident_key"] if lineage else None,
        "generation_run_id": lineage["generation_run_id"] if lineage else None,
        "generation_fingerprint": lineage["generation_fingerprint"] if lineage else None,
        "source_ingestion_run_id": lineage["source_ingestion_run_id"] if lineage else None,
        "canonical_fingerprint": lineage["canonical_fingerprint"] if lineage else None,
        "source_dataset": lineage["source_dataset"] if lineage else None,
        "source_sha256": lineage["source_sha256"] if lineage else None,
        "layers": {
            "observed": "transactions (amounts, statuses) — immutable",
            "synthetic": "gateways, profiles, routing, health — modelled infrastructure",
            "simulated": "incident outcomes and counterfactual projections — modelled",
            "answer_key": "EXCLUDED — evaluation only; never read by any Day 4 module",
            "agent_conclusion": "recommendation rationale — NULL in Day 4A (no agent)",
        },
    }
