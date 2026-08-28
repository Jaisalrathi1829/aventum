"""
The recommendation builder.

READ `build_recommendation`'s SIGNATURE. IT IS THE SECURITY PROPERTY.
---------------------------------------------------------------------
    build_recommendation(session, *, simulation_id, analysis_run_id, world,
                         alert_role, rationale=None, agent_run_id=None)

There is no `expected_gmv_retained` parameter. No `risk_score`. No `confidence`. No
`traffic_percentage`. No numeric parameter of any kind. Every quantitative field on the
persisted row is READ SERVER-SIDE from the simulation identified by `simulation_id` and
from the RCA row for `analysis_run_id`.

This is deliberately not "validate the caller's numbers against the simulation". A
validation can be skipped, mis-scoped, or fooled by a rounding tolerance. An absent
parameter cannot be passed. A caller who wants different numbers must first persist a
different simulation -- which is fingerprinted, idempotent, and auditable.

The only caller-supplied content is `rationale`: free text, no numeric authority,
NULL throughout Day 4A. When Day 4B attaches an LLM, that field is the entire surface
it can write to.

POLICY RUNS HERE, NOT AFTER
---------------------------
The builder validates before it persists, so a recommendation is born either PERMITTED
or BLOCKED and never exists in an unvalidated state that something downstream might
mistake for approved. A BLOCKED row is still persisted, with its reason codes -- a
refusal is a result worth auditing, not an error to discard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from aventum_counterfactual.constants import ACTION_NO_ACTION, CAPACITY_UNAVAILABLE
from aventum_counterfactual.fingerprint import compute_recommendation_fingerprint
from aventum_counterfactual.models import CounterfactualSimulation
from aventum_counterfactual.source import WorldState, load_rca
from aventum_policy import POLICY_VERSION
from aventum_policy.constants import (
    RECOMMENDATION_TTL_MINUTES,
    RESULT_BLOCKED,
    RESULT_PERMITTED,
)
from aventum_policy.gate import PolicyDecision, validate

from . import ACTION_MODEL_VERSION
from .audit import (
    POLICY_VALIDATED,
    RECOMMENDATION_BLOCKED,
    RECOMMENDATION_CREATED,
    ACTOR_SYSTEM,
    emit,
    ref,
)
from .models import Recommendation

# ------------------------------------------------------------- state machine
# Forward-only. Every legal transition is listed; anything absent raises.
LEGAL_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "DRAFT": ("PERMITTED", "BLOCKED", "ABANDONED"),
    "PERMITTED": ("AWAITING_APPROVAL", "SUPERSEDED", "EXPIRED", "ABANDONED"),
    "AWAITING_APPROVAL": ("APPROVED", "REJECTED", "EXPIRED", "ABANDONED"),
    "APPROVED": ("EXECUTED", "EXPIRED", "ABANDONED"),
    # Terminal states. A BLOCKED recommendation never reaches approval.
    "BLOCKED": (),
    "EXECUTED": (),
    "REJECTED": (),
    "EXPIRED": (),
    "SUPERSEDED": (),
    "ABANDONED": (),
}


class RecommendationStateError(RuntimeError):
    """Raised on an illegal lifecycle transition."""


class RecommendationInputError(RuntimeError):
    """Raised when the referenced simulation or analysis run does not resolve."""


def advance_status(recommendation: Recommendation, new_status: str) -> Recommendation:
    """
    Move a recommendation forward, or refuse.

    Refusing loudly matters: silently permitting a backward transition would let an
    EXECUTED recommendation return to AWAITING_APPROVAL and be approved a second time.
    """
    current = recommendation.status
    allowed = LEGAL_TRANSITIONS.get(current, ())
    if new_status not in allowed:
        raise RecommendationStateError(
            f"illegal transition {current} -> {new_status}; "
            f"legal from {current}: {allowed or '(terminal)'}"
        )
    recommendation.status = new_status
    return recommendation


@dataclass(frozen=True)
class RecommendationResult:
    recommendation: Recommendation
    decision: PolicyDecision

    @property
    def permitted(self) -> bool:
        return self.decision.permitted


def _fingerprint_fields(sim: CounterfactualSimulation, rca: dict | None) -> dict:
    """
    The decision-relevant content an approval binds to.

    Every field here is one a human's decision could reasonably depend on. Changing any
    of them after approval invalidates the approval, which is what stops an approval
    from being carried over to a materially different proposal.
    """
    return {
        "simulation_id": sim.simulation_id,
        "action_type": sim.action_type,
        "source_gateway_id": sim.source_gateway_id or "",
        "target_gateway_id": sim.target_gateway_id or "",
        "traffic_percentage": f"{float(sim.traffic_percentage or 0):.2f}",
        "expected_gmv_retained": f"{float(sim.projected_gmv_retained or 0):.2f}",
        "expected_success_delta": f"{float(sim.expected_success_delta or 0):.6f}",
        "expected_latency_delta_ms": f"{float(sim.latency_delta_ms or 0):.2f}",
        "risk_score": f"{float(sim.risk_score or 0):.6f}",
        "input_fingerprint": sim.input_fingerprint,
        "simulation_fingerprint": sim.simulation_fingerprint or "",
        "confidence": f"{float((rca or {}).get('confidence') or 0):.4f}",
        "evidence_strength": f"{float((rca or {}).get('evidence_strength') or 0):.4f}",
        "significance_sigma": f"{float((rca or {}).get('significance_sigma') or 0):.4f}",
        "severity": (rca or {}).get("severity") or "",
        "policy_version": POLICY_VERSION,
    }


def build_recommendation(
    session: Session,
    *,
    simulation_id: int,
    analysis_run_id: int,
    world: WorldState,
    alert_role: str | None,
    rationale: str | None = None,
    agent_run_id: int | None = None,
    alternatives: list[dict] | None = None,
    now: datetime | None = None,
) -> RecommendationResult:
    """
    Build and persist a recommendation from a PERSISTED simulation.

    NOTE THE ABSENT PARAMETERS. There is no way to pass a GMV figure, a risk score, a
    confidence, or a traffic percentage. All of them are read from `simulation_id` and
    from the RCA row below.

    Idempotent on (incident_id, simulation_id, policy_version): rebuilding returns the
    existing row rather than minting a second recommendation for the same candidate.
    """
    now = now or datetime.now(timezone.utc)

    sim = session.get(CounterfactualSimulation, simulation_id)
    if sim is None:
        raise RecommendationInputError(f"simulation {simulation_id} does not resolve")

    existing = (
        session.query(Recommendation)
        .filter_by(
            incident_id=sim.incident_id,
            simulation_id=sim.simulation_id,
            policy_version=POLICY_VERSION,
        )
        .one_or_none()
    )
    if existing is not None:
        decision = validate(sim, load_rca(session, analysis_run_id), world, alert_role, now=now)
        return RecommendationResult(recommendation=existing, decision=decision)

    # RCA values are READ, never passed in. `load_rca` selects no ground-truth column.
    rca = load_rca(session, analysis_run_id)

    decision = validate(sim, rca, world, alert_role, now=now)
    emit(
        session,
        event_type=POLICY_VALIDATED,
        actor=ACTOR_SYSTEM,
        incident_id=sim.incident_id,
        input_ref=ref("counterfactual_simulations", sim.simulation_id),
        payload=decision.as_dict(),
    )

    status = "PERMITTED" if decision.permitted else "BLOCKED"
    fingerprint = compute_recommendation_fingerprint(_fingerprint_fields(sim, rca))

    recommendation = Recommendation(
        incident_id=sim.incident_id,
        analysis_run_id=analysis_run_id,
        simulation_id=sim.simulation_id,
        agent_run_id=agent_run_id,
        action_type=sim.action_type,
        source_gateway_id=sim.source_gateway_id,
        target_gateway_id=sim.target_gateway_id,
        traffic_percentage=sim.traffic_percentage,
        # ---- every number below is copied from the simulation row ----
        expected_success_delta=sim.expected_success_delta,
        expected_gmv_retained=sim.projected_gmv_retained,
        expected_latency_delta_ms=sim.latency_delta_ms,
        risk_score=sim.risk_score,
        risk_components=sim.risk_components,
        # ---- and from the RCA row ----
        confidence=(rca or {}).get("confidence"),
        evidence_strength=(rca or {}).get("evidence_strength"),
        significance_sigma=(rca or {}).get("significance_sigma"),
        severity=(rca or {}).get("severity"),
        supporting_evidence_ids=list((rca or {}).get("supporting_evidence_ids") or []),
        alternatives_considered=alternatives or [],
        # ---- the only caller-authored field ----
        rationale=rationale,
        policy_validation_result=RESULT_PERMITTED if decision.permitted else RESULT_BLOCKED,
        policy_reason_codes=(None if decision.permitted else {"codes": decision.reason_codes}),
        constraints=decision.constraints_in_force(),
        status=status,
        expires_at=now + timedelta(minutes=RECOMMENDATION_TTL_MINUTES),
        recommendation_fingerprint=fingerprint,
        policy_version=POLICY_VERSION,
        model_version=ACTION_MODEL_VERSION,
    )
    session.add(recommendation)
    session.flush()

    emit(
        session,
        event_type=RECOMMENDATION_CREATED if decision.permitted else RECOMMENDATION_BLOCKED,
        actor=ACTOR_SYSTEM,
        incident_id=sim.incident_id,
        input_ref=ref("counterfactual_simulations", sim.simulation_id),
        output_ref=ref("recommendations", recommendation.recommendation_id),
        payload={
            "action_type": sim.action_type,
            "target_gateway_id": sim.target_gateway_id,
            "traffic_percentage": float(sim.traffic_percentage or 0),
            "expected_gmv_retained": float(sim.projected_gmv_retained or 0),
            "policy_result": recommendation.policy_validation_result,
            "reason_codes": decision.reason_codes,
            "numbers_sourced_from": f"counterfactual_simulations#{sim.simulation_id}",
            "capacity": CAPACITY_UNAVAILABLE,
        },
        fingerprint=fingerprint,
    )
    return RecommendationResult(recommendation=recommendation, decision=decision)


def is_expired(recommendation: Recommendation, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return now > recommendation.expires_at


def requires_approval(recommendation: Recommendation) -> bool:
    """
    NO_ACTION terminates at PERMITTED and needs no approval, because it changes nothing.

    That is a SUCCESSFUL terminal state, not a degraded one -- the system deciding to do
    nothing, on evidence, is the outcome the whole NO_ACTION-as-baseline design exists
    to make reachable.
    """
    return recommendation.action_type != ACTION_NO_ACTION
