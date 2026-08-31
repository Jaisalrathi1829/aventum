"""
Day 5 batch recovery measurement (§19).

One incident that went well is an anecdote. This aggregates the whole persisted
population of incidents, recommendations, approvals, actions and verifications so the
system can be judged on what it actually did across every case it saw -- including the
ones where it correctly decided to do nothing, and the ones where policy stopped it.

EVERY FIGURE IS COUNTED FROM PERSISTED ROWS. Nothing here is estimated, extrapolated,
or back-filled. A metric the database cannot support is returned as `UNAVAILABLE`
rather than approximated, per §19.

The two money figures are deliberately kept apart and are never summed together:

    total_projected_gmv_retained   what simulations PROJECTED, over recommendations
    total_actual_gmv_recovered     what verification MEASURED, over verified actions

They answer different questions and have different epistemic standing. A UI that adds
them, or shows one under the other's label, is misreporting the system.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aventum_action.models import Action, Approval, Recommendation

from .constants import (
    PARTIALLY_EFFECTIVE,
    RECOVERY_CLAIM_NOTE,
    RECOVERY_EFFECTIVE,
    RECOVERY_NOT_VERIFIED,
    VERIFICATION_COMPLETE,
    VERIFICATION_PROVENANCE,
)
from .models import Verification

UNAVAILABLE = "UNAVAILABLE"


@dataclass
class BatchRecoverySummary:
    """Population-level counts and business metrics, all from persisted rows."""

    # ---- counts -------------------------------------------------------------------
    incidents_evaluated: int = 0
    interventions_proposed: int = 0
    no_action_count: int = 0
    policy_blocked_count: int = 0
    approvals_requested: int = 0
    approvals_granted: int = 0
    approvals_rejected: int = 0
    approvals_expired: int = 0
    interventions_executed: int = 0
    executions_rejected: int = 0
    interventions_verified: int = 0
    recovery_effective_count: int = 0
    partially_effective_count: int = 0
    recovery_not_verified_count: int = 0

    # ---- business metrics ----------------------------------------------------------
    total_projected_gmv_retained: float = 0.0
    total_actual_gmv_recovered: float = 0.0
    recovery_uplift: object = UNAVAILABLE
    verification_success_rate: object = UNAVAILABLE
    intervention_rate: object = UNAVAILABLE
    no_action_rate: object = UNAVAILABLE
    transactions_moved: int = 0

    provenance: str = VERIFICATION_PROVENANCE
    recovery_claim: str = RECOVERY_CLAIM_NOTE
    notes: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _ratio(numerator: int, denominator: int) -> object:
    """A rate, or UNAVAILABLE when there is nothing to divide by.

    Returning 0.0 for an empty population would read as "we tried and failed" on a
    dashboard, which is a different claim from "we have not tried yet".
    """
    if denominator <= 0:
        return UNAVAILABLE
    return round(numerator / denominator, 6)


def build_batch_summary(session: Session) -> BatchRecoverySummary:
    """Aggregate the entire persisted decision population."""
    s = BatchRecoverySummary()

    # ---- recommendations: what was proposed, and what was declined or blocked --------
    rows = session.execute(
        select(Recommendation.action_type, Recommendation.policy_validation_result,
               Recommendation.status, Recommendation.expected_gmv_retained,
               Recommendation.incident_id)
    ).all()

    incidents: set[int] = set()
    for action_type, policy_result, _status, gmv, incident_id in rows:
        if incident_id is not None:
            incidents.add(incident_id)
        if action_type == "NO_ACTION":
            s.no_action_count += 1
            continue
        s.interventions_proposed += 1
        # A recommendation the policy refused is a stop, not a proposal that failed.
        if policy_result and policy_result.upper() not in ("PERMITTED", "PERMIT"):
            s.policy_blocked_count += 1
        if gmv is not None:
            s.total_projected_gmv_retained += float(gmv)

    s.incidents_evaluated = len(incidents)
    s.total_projected_gmv_retained = round(s.total_projected_gmv_retained, 2)

    # ---- approvals ------------------------------------------------------------------
    approval_counts = dict(
        session.execute(
            select(Approval.status, func.count()).group_by(Approval.status)
        ).all()
    )
    s.approvals_requested = sum(approval_counts.values())
    s.approvals_granted = approval_counts.get("APPROVED", 0)
    s.approvals_rejected = approval_counts.get("REJECTED", 0)
    s.approvals_expired = approval_counts.get("EXPIRED", 0)

    # ---- actions ---------------------------------------------------------------------
    action_counts = dict(
        session.execute(select(Action.status, func.count()).group_by(Action.status)).all()
    )
    s.interventions_executed = action_counts.get("EXECUTED", 0)
    s.executions_rejected = action_counts.get("REJECTED", 0)

    # ---- verifications ----------------------------------------------------------------
    verifications = session.execute(
        select(Verification.status, Verification.outcome,
               Verification.actual_gmv_recovered, Verification.transactions_moved)
    ).all()

    for status, outcome, gmv_recovered, moved in verifications:
        if status != VERIFICATION_COMPLETE:
            continue
        s.interventions_verified += 1
        if outcome == RECOVERY_EFFECTIVE:
            s.recovery_effective_count += 1
        elif outcome == PARTIALLY_EFFECTIVE:
            s.partially_effective_count += 1
        elif outcome == RECOVERY_NOT_VERIFIED:
            s.recovery_not_verified_count += 1
        # Only a verification that actually established recovery contributes recovered
        # GMV. Counting the not-verified ones would inflate the headline with money the
        # system just finished saying it could not confirm.
        if outcome in (RECOVERY_EFFECTIVE, PARTIALLY_EFFECTIVE) and gmv_recovered is not None:
            s.total_actual_gmv_recovered += float(gmv_recovered)
        if moved:
            s.transactions_moved += int(moved)

    s.total_actual_gmv_recovered = round(s.total_actual_gmv_recovered, 2)

    # ---- rates ------------------------------------------------------------------------
    total_decisions = s.interventions_proposed + s.no_action_count
    s.intervention_rate = _ratio(s.interventions_proposed, total_decisions)
    s.no_action_rate = _ratio(s.no_action_count, total_decisions)
    s.verification_success_rate = _ratio(
        s.recovery_effective_count + s.partially_effective_count, s.interventions_verified
    )

    # Uplift compares what was MEASURED against what was PROJECTED -- the honest
    # question a reviewer asks of any model that authorised an intervention.
    if s.total_projected_gmv_retained > 0 and s.interventions_verified > 0:
        s.recovery_uplift = round(
            s.total_actual_gmv_recovered / s.total_projected_gmv_retained, 6
        )
    else:
        s.recovery_uplift = UNAVAILABLE

    s.notes = {
        "projected_vs_actual": (
            "total_projected_gmv_retained is a SIMULATION projection over "
            "recommendations; total_actual_gmv_recovered is a VERIFIED measurement over "
            "executed actions. They are different populations and must never be summed."
        ),
        "no_action_is_a_result": (
            "NO_ACTION and policy-blocked outcomes are counted as successful stops, not "
            "as failures to act."
        ),
        "recovered_gmv_excludes_unverified": (
            "Actions whose verification returned RECOVERY_NOT_VERIFIED contribute zero "
            "recovered GMV."
        ),
    }
    return s
