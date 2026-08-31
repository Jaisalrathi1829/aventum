"""
Human approval. Append-only, fingerprint-bound, expiring, human-attributed.

WHAT THE PAYLOAD IS FOR
-----------------------
`build_approval_payload()` must let a human decide WITHOUT reading any agent reasoning.
That constraint shapes the whole structure: benefit and risk are stated with their basis,
the four Day 3 decision inputs travel separately rather than as one score, the rejected
alternatives are listed with the specific reason each lost, and every policy gate is
shown with the value it saw.

It also carries `provenance: "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION"` as a first-class
field. The approver is told, inside the artifact they are approving, that the incident is
synthetic and the execution will be simulated -- so the honesty boundary survives the
payload being screenshotted, exported, or pasted into a ticket without this docstring.

WHY DECISIONS ARE ROWS, NOT UPDATES
------------------------------------
An approval is never mutated from PENDING to APPROVED in place... except that it is, and
deliberately: the PENDING row IS the request, and deciding it stamps the decision onto
the same row. What is append-only is the SEQUENCE -- a re-validation cycle after expiry
creates a NEW approval row rather than resetting the old one, so "approved at 10:04,
expired, re-approved at 10:31 against changed content" stays fully reconstructable. The
partial unique index permits only one PENDING row at a time, so this can never race.

Qwen cannot approve. In Day 4A no model exists at all; in Day 4B there is no tool, no
code path, and no column an agent can write to reach a decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from aventum_counterfactual.constants import CAPACITY_UNAVAILABLE, ELIGIBILITY_UNCONDITIONAL
from aventum_counterfactual.models import CounterfactualSimulation
from aventum_policy.constants import APPROVAL_TTL_MINUTES
from aventum_policy.gate import PolicyDecision

from .audit import (
    ACTOR_SYSTEM,
    APPROVAL_DECIDED,
    APPROVAL_REQUESTED,
    emit,
    human_actor,
    ref,
)
from .models import Approval, Recommendation
from .recommendation import advance_status, is_expired, requires_approval

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"


class ApprovalError(RuntimeError):
    """Raised when an approval is requested or decided out of order."""


def build_approval_payload(
    session: Session,
    recommendation: Recommendation,
    decision: PolicyDecision | None = None,
) -> dict:
    """
    Everything a human needs, and nothing that requires trusting a model.

    Note that `expected_risk` names capacity explicitly as UNAVAILABLE rather than
    omitting it. An absent field reads as "no capacity concern"; the explicit marker
    reads as "this was not checked, because it cannot be" -- which is the truth.
    """
    sim = session.get(CounterfactualSimulation, recommendation.simulation_id)
    return {
        "recommendation_id": recommendation.recommendation_id,
        "incident_id": recommendation.incident_id,
        "proposed_action": recommendation.action_type,
        "source_gateway": recommendation.source_gateway_id,
        "target_gateway": recommendation.target_gateway_id,
        "traffic_percentage": float(recommendation.traffic_percentage or 0),
        "expected_benefit": {
            "gmv_retained": float(recommendation.expected_gmv_retained or 0),
            "success_delta": float(recommendation.expected_success_delta or 0),
            # Says exactly which half is measured and which half is modelled.
            "basis": "OBSERVED_TRANSACTION_AMOUNTS + MODELLED_OUTCOMES",
            "note": "Projected GMV retained — NOT recovered GMV. No action has occurred.",
        },
        "expected_risk": {
            "latency_delta_ms": float(recommendation.expected_latency_delta_ms or 0),
            "concentration_after": float(sim.concentration_after or 0) if sim else None,
            "risk_score": float(recommendation.risk_score or 0),
            "risk_components": recommendation.risk_components,
            "capacity": CAPACITY_UNAVAILABLE,
            "eligibility_basis": ELIGIBILITY_UNCONDITIONAL,
        },
        # The Day 3 quartet, presented separately. A human sees four independent
        # signals, exactly as the policy gate required them.
        "decision_inputs": {
            "confidence": float(recommendation.confidence or 0),
            "evidence_strength": float(recommendation.evidence_strength or 0),
            "significance_sigma": float(recommendation.significance_sigma or 0),
            "severity": recommendation.severity,
        },
        "evidence_refs": list(recommendation.supporting_evidence_ids or []),
        "simulation_id": recommendation.simulation_id,
        "simulation_fingerprint": sim.simulation_fingerprint if sim else None,
        "input_fingerprint": sim.input_fingerprint if sim else None,
        "alternatives_rejected": recommendation.alternatives_considered or [],
        "gates": (decision.as_dict()["gates"] if decision else None),
        "constraints": recommendation.constraints,
        "expires_at": recommendation.expires_at.isoformat(),
        "recommendation_fingerprint": recommendation.recommendation_fingerprint,
        # Stated inside the artifact, so it survives being exported anywhere.
        "provenance": "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION",
        "honesty_note": (
            "This incident is synthetic and was injected by Aventum. Execution is "
            "simulated through SimulatedRoutingAdapter. No real payment infrastructure "
            "is contacted and no real gateway is rerouted."
        ),
    }


def request_approval(
    session: Session,
    recommendation: Recommendation,
    decision: PolicyDecision | None = None,
    now: datetime | None = None,
) -> Approval:
    """
    Raise a PENDING approval for a PERMITTED, non-NO_ACTION recommendation.

    Refuses a BLOCKED recommendation outright: a blocked proposal must never reach a
    human, because presenting it invites an approval the policy already refused.
    """
    now = now or datetime.now(timezone.utc)

    if recommendation.policy_validation_result != "PERMITTED":
        raise ApprovalError(
            f"recommendation {recommendation.recommendation_id} is "
            f"{recommendation.policy_validation_result}; a blocked recommendation is "
            "never presented for approval"
        )
    if not requires_approval(recommendation):
        raise ApprovalError(
            "NO_ACTION requires no approval — it changes nothing and terminates at PERMITTED"
        )
    if is_expired(recommendation, now):
        raise ApprovalError(f"recommendation {recommendation.recommendation_id} has expired")

    # Enforced in application code for a clear error AND by the partial unique index,
    # which is what actually makes it race-proof.
    outstanding = session.scalar(
        select(Approval).where(
            Approval.recommendation_id == recommendation.recommendation_id,
            Approval.status == STATUS_PENDING,
        )
    )
    if outstanding is not None:
        raise ApprovalError(
            f"approval {outstanding.approval_id} is already pending for "
            f"recommendation {recommendation.recommendation_id}"
        )

    if recommendation.status == "PERMITTED":
        advance_status(recommendation, "AWAITING_APPROVAL")

    payload = build_approval_payload(session, recommendation, decision)
    approval = Approval(
        recommendation_id=recommendation.recommendation_id,
        status=STATUS_PENDING,
        requested_at=now,
        # Deliberately shorter than the recommendation TTL: an approval is a judgement
        # about a CURRENT world, so it should lapse before the thing it approves.
        expires_at=now + timedelta(minutes=APPROVAL_TTL_MINUTES),
        approval_fingerprint=recommendation.recommendation_fingerprint,
        payload=payload,
    )
    session.add(approval)
    session.flush()

    emit(
        session,
        event_type=APPROVAL_REQUESTED,
        actor=ACTOR_SYSTEM,
        incident_id=recommendation.incident_id,
        input_ref=ref("recommendations", recommendation.recommendation_id),
        output_ref=ref("approvals", approval.approval_id),
        payload={
            "expires_at": approval.expires_at.isoformat(),
            "action_type": recommendation.action_type,
            "target_gateway_id": recommendation.target_gateway_id,
            "provenance": "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION",
        },
        fingerprint=approval.approval_fingerprint,
    )
    return approval


def decide_approval(
    session: Session,
    approval: Approval,
    *,
    decision: str,
    approver_identity: str,
    note: str | None = None,
    now: datetime | None = None,
) -> Approval:
    """
    Record a HUMAN decision. `approver_identity` is mandatory and DB-enforced.

    An expired approval is stamped EXPIRED rather than silently accepted: the remedy is
    a new approval against re-validated content, never a late decision on stale facts.
    """
    now = now or datetime.now(timezone.utc)

    if decision not in (STATUS_APPROVED, STATUS_REJECTED):
        raise ApprovalError(f"decision must be {STATUS_APPROVED} or {STATUS_REJECTED}")

    # Serialise concurrent decisions on this approval BEFORE reading its status.
    #
    # The terminal-status check below is a read-then-write, and without a lock every
    # concurrent caller loads the row, all see PENDING, all pass, and all emit an
    # APPROVAL_DECIDED event. Measured: five simultaneous requests produced five audit
    # events for one human decision -- and a browser double-click produced two, because
    # both clicks fire before React can disable the button. The final row was correct;
    # the AUDIT TRAIL was not, which is worse, because the audit is what the product
    # asks anyone to trust.
    #
    # `FOR UPDATE` makes PostgreSQL queue the losers until the winner commits;
    # `populate_existing` forces the refreshed row into the identity map, so the loser
    # sees APPROVED rather than the stale PENDING it loaded a moment earlier and takes
    # the terminal-status branch below.
    #
    # This is the same shape of guarantee `uq_action_idempotency` and
    # `uq_verification_identity` already give execution and verification. The approval
    # decision was the one transition in the state machine with no equivalent guard.
    approval = session.execute(
        select(Approval)
        .where(Approval.approval_id == approval.approval_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()

    if approval.status != STATUS_PENDING:
        raise ApprovalError(
            f"approval {approval.approval_id} is already {approval.status}; "
            "decisions are terminal and a new cycle creates a new approval row"
        )
    if not approver_identity:
        raise ApprovalError("an approval must be attributed to a human identity")

    recommendation = session.get(Recommendation, approval.recommendation_id)

    if now > approval.expires_at:
        approval.status = STATUS_EXPIRED
        session.flush()
        emit(
            session,
            event_type="APPROVAL_EXPIRED",
            actor=ACTOR_SYSTEM,
            incident_id=recommendation.incident_id if recommendation else None,
            input_ref=ref("approvals", approval.approval_id),
            payload={"expired_at": approval.expires_at.isoformat(), "attempted_at": now.isoformat()},
        )
        raise ApprovalError(
            f"approval {approval.approval_id} expired at {approval.expires_at.isoformat()}; "
            "re-simulate, re-validate, and request a new approval"
        )

    approval.status = decision
    approval.decided_at = now
    approval.approver_identity = approver_identity
    approval.decision_note = note

    if recommendation is not None:
        advance_status(recommendation, "APPROVED" if decision == STATUS_APPROVED else "REJECTED")
    session.flush()

    emit(
        session,
        event_type=APPROVAL_DECIDED,
        actor=human_actor(approver_identity),
        incident_id=recommendation.incident_id if recommendation else None,
        input_ref=ref("recommendations", approval.recommendation_id),
        output_ref=ref("approvals", approval.approval_id),
        payload={
            "decision": decision,
            "approver_identity": approver_identity,
            "note": note,
            "decided_at": now.isoformat(),
        },
        fingerprint=approval.approval_fingerprint,
    )
    return approval


def expire_stale_approvals(session: Session, now: datetime | None = None) -> int:
    """Stamp lapsed PENDING approvals EXPIRED. Returns how many were closed."""
    now = now or datetime.now(timezone.utc)
    stale = session.scalars(
        select(Approval).where(Approval.status == STATUS_PENDING, Approval.expires_at < now)
    ).all()
    for approval in stale:
        approval.status = STATUS_EXPIRED
        recommendation = session.get(Recommendation, approval.recommendation_id)
        emit(
            session,
            event_type="APPROVAL_EXPIRED",
            actor=ACTOR_SYSTEM,
            incident_id=recommendation.incident_id if recommendation else None,
            input_ref=ref("approvals", approval.approval_id),
            payload={"expired_at": approval.expires_at.isoformat()},
        )
    session.flush()
    return len(stale)
