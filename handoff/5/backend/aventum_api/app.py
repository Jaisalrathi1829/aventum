"""
The Aventum HTTP API.

This is the ONLY surface the browser is permitted to reach. It exposes no SQL, no query
capability, no connection string, no filesystem path and no Ollama control -- §28. Every
state-changing request is validated server-side by the same Day 4A modules the CLI uses,
so a forged frontend gains nothing: the browser can ask, and the backend decides.

WHAT THIS LAYER IS AND IS NOT
-----------------------------
It is a thin, typed translation between HTTP and the existing deterministic spine. It
contains no business logic of its own: no thresholds, no arithmetic on money, no policy
evaluation, no decision about whether an action is permitted. Where it looks like it is
deciding something, it is reading a decision that `aventum_policy`, `aventum_action` or
`aventum_verification` already made and persisted.

That restraint is the point. Business logic that leaks into the transport layer is
business logic no test in Days 2-4 is guarding.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Body, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aventum_action.approval import (
    ApprovalError,
    decide_approval,
    expire_stale_approvals,
    request_approval,
)
from aventum_action.execute import execute_action
from aventum_action.models import Action, Approval, AuditEvent, Recommendation
from aventum_action.pipeline import run_decision_pipeline
from aventum_action.recommendation import is_expired
from aventum_counterfactual.models import CounterfactualSimulation
from aventum_counterfactual.source import load_world_state
from aventum_incident.handoff import build_handoff
from aventum_verification.batch import build_batch_summary
from aventum_verification.verify import get_verification, verify_action

from . import serializers as ser
from .config import load_api_config
from .deps import ApiError, bad_request, conflict, get_session, not_found

log = logging.getLogger("aventum.api")

API_VERSION = "day5-v1"

app = FastAPI(
    title="Aventum API",
    version=API_VERSION,
    description=(
        "Payment incident intelligence. Synthetic infrastructure, simulated execution, "
        "no live routing changes."
    ),
)

_config = load_api_config()

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_config.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------- errors
@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """
    The last line of §29: no stack trace, no SQL, no database detail reaches the browser.

    The real exception is logged server-side where an operator can find it; the client
    receives a stable code and a sentence.
    """
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "The request could not be completed. The error has been logged.",
        },
    )


# ---------------------------------------------------------------------------- health
@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    """
    Liveness for the API, the database and the agent, reported separately.

    The agent is checked but is explicitly NOT required: §29/§27 demand that Qwen being
    down degrades the product rather than breaking it, so its unavailability is a field
    in a 200 response, not a 503.
    """
    db_ok, db_detail = True, "connected"
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        db_ok, db_detail = False, "unreachable"

    agent_ok, agent_detail = _agent_availability()

    return {
        "api": {"ok": True, "version": API_VERSION},
        "database": {"ok": db_ok, "detail": db_detail},
        # Deterministic analysis does not depend on this being true.
        "agent": {"ok": agent_ok, "detail": agent_detail, "required": False},
        "environment": ser.ENVIRONMENT_NOTICE,
    }


# Probing a DOWN Ollama costs ~4 s on this platform, because an unreachable host is
# discovered by timeout rather than by refusal. Health is polled every 20 s by every open
# tab, so an uncached probe made the endpoint the slowest thing in the product and pushed
# concurrent callers past their own client timeouts.
#
# The probe is still real -- it is just not repeated more than once per window.
_AGENT_PROBE_TTL_S = 15.0
_agent_probe_cache: tuple[float, bool, str] | None = None


def _agent_availability() -> tuple[bool, str]:
    """Whether Ollama answered recently. Cached briefly; never fabricated."""
    global _agent_probe_cache

    if not _config.agent_enabled:
        return False, "disabled"

    now = time.monotonic()
    if _agent_probe_cache is not None and now - _agent_probe_cache[0] < _AGENT_PROBE_TTL_S:
        return _agent_probe_cache[1], _agent_probe_cache[2]

    try:
        from aventum_agent.client import OllamaClient

        ok = OllamaClient().is_available()
        detail = "available" if ok else "unavailable"
    except Exception:
        ok, detail = False, "unavailable"

    _agent_probe_cache = (now, ok, detail)
    return ok, detail


def _commit(session: Session) -> None:
    """
    Make a write durable before the response goes out.

    `get_session` also commits, but FastAPI runs a yield-dependency's cleanup AFTER the
    response has been sent. A browser that re-reads the moment a mutation returns can
    therefore observe pre-commit state -- which is exactly what happened: the agent run
    was written, the POST returned, the UI re-read immediately and saw nothing, and the
    panel fell back to "no agent run exists for this incident".

    Committing here closes that window, so read-after-write holds for any client fast
    enough to try.
    """
    session.commit()


# -------------------------------------------------------------------------- overview
@router.get("/overview")
def overview(session: Session = Depends(get_session)) -> dict:
    """
    The operations answer to "what is happening right now".

    Recovery state is DERIVED FROM PERSISTED ROWS on every request, never stored as a
    status column and never seeded with an initial value (§12). Two browsers asking at
    the same moment get the same answer because the answer is a function of the database.
    """
    incidents = session.execute(
        text(
            "SELECT i.incident_id, i.incident_name, i.incident_type, i.status, "
            "       i.affected_gateway_id, i.incident_start, i.incident_end, "
            "       r.analysis_run_id, r.severity, r.confidence, r.significance_sigma, "
            "       r.evidence_strength, r.verdict, r.predicted_root_cause "
            "FROM incidents i "
            "LEFT JOIN incident_analysis_runs ar ON ar.incident_id = i.incident_id "
            "LEFT JOIN incident_rca_results r ON r.analysis_run_id = ar.analysis_run_id "
            "ORDER BY r.significance_sigma DESC NULLS LAST, i.incident_id"
        )
    ).mappings().all()

    seen: set[int] = set()
    rows = []
    for r in incidents:
        if r["incident_id"] in seen:
            continue
        seen.add(r["incident_id"])
        rows.append(
            {
                "incident_id": r["incident_id"],
                "incident_name": r["incident_name"],
                "incident_type": r["incident_type"],
                "status": r["status"],
                "affected_gateway_id": r["affected_gateway_id"],
                "window_start": ser.iso(r["incident_start"]),
                "window_end": ser.iso(r["incident_end"]),
                "analysis_run_id": r["analysis_run_id"],
                "severity": r["severity"],
                "confidence": ser.num(r["confidence"]),
                "significance_sigma": ser.num(r["significance_sigma"]),
                "evidence_strength": ser.num(r["evidence_strength"]),
                "verdict": r["verdict"],
                "predicted_root_cause": r["predicted_root_cause"],
                "truth": ser.SYNTHETIC,
            }
        )

    primary = rows[0] if rows else None
    recovery = _recovery_state(session, primary["incident_id"]) if primary else {
        "state": "NO_ACTIVE_ACTION",
        "detail": "No incident is currently under analysis.",
    }

    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "incidents": rows,
        "active_incident_count": sum(1 for r in rows if r["status"] in ("ACTIVE", "DIAGNOSED")),
        "primary_incident": primary,
        "recovery": recovery,
        "batch": build_batch_summary(session).as_dict(),
    }


def _recovery_state(session: Session, incident_id: int) -> dict:
    """
    Where this incident stands in the recovery lifecycle, computed from what exists.

    The ordering is deliberate and runs backwards from the furthest-progressed artefact:
    a verified action is further along than an executed one, which is further than an
    approval, which is further than a recommendation. Reading forwards would report
    "awaiting approval" for an incident that had already been executed and verified.
    """
    rec = session.scalars(
        select(Recommendation)
        .where(Recommendation.incident_id == incident_id)
        .order_by(Recommendation.recommendation_id.desc())
    ).first()
    if rec is None:
        return {"state": "NO_ACTIVE_ACTION", "detail": "No recommendation has been produced."}

    action = session.scalars(
        select(Action)
        .where(Action.recommendation_id == rec.recommendation_id)
        .order_by(Action.action_id.desc())
    ).first()

    if action is not None:
        verification = get_verification(session, action.action_id)
        if verification is not None and verification.status == "COMPLETE":
            return {
                "state": "VERIFIED",
                "outcome": verification.outcome,
                "detail": f"Independent verification returned {verification.outcome}.",
                "action_id": action.action_id,
                "recommendation_id": rec.recommendation_id,
            }
        if action.status == "EXECUTED":
            return {
                "state": "VERIFYING",
                "detail": "Action executed. Independent verification has not yet run.",
                "action_id": action.action_id,
                "recommendation_id": rec.recommendation_id,
            }
        if action.status == "REJECTED":
            return {
                "state": "EXECUTION_REJECTED",
                "detail": action.rejection_reason or "Execution was rejected at revalidation.",
                "action_id": action.action_id,
                "recommendation_id": rec.recommendation_id,
            }
        return {
            "state": action.status,
            "detail": f"Action is {action.status}.",
            "action_id": action.action_id,
            "recommendation_id": rec.recommendation_id,
        }

    if rec.action_type == "NO_ACTION":
        return {
            "state": "NO_ACTION",
            "detail": "The deterministic decision is to take no action.",
            "recommendation_id": rec.recommendation_id,
        }
    if rec.policy_validation_result and rec.policy_validation_result.upper() not in (
        "PERMITTED",
        "PERMIT",
    ):
        return {
            "state": "POLICY_BLOCKED",
            "detail": f"Policy returned {rec.policy_validation_result}.",
            "recommendation_id": rec.recommendation_id,
        }

    approval = session.scalars(
        select(Approval)
        .where(Approval.recommendation_id == rec.recommendation_id)
        .order_by(Approval.approval_id.desc())
    ).first()
    if approval is None:
        return {
            "state": "AWAITING_APPROVAL_REQUEST",
            "detail": "A recommendation exists but approval has not been requested.",
            "recommendation_id": rec.recommendation_id,
        }
    if approval.status == "PENDING":
        return {
            "state": "AWAITING_APPROVAL",
            "detail": "A human decision is required.",
            "recommendation_id": rec.recommendation_id,
            "approval_id": approval.approval_id,
        }
    if approval.status == "REJECTED":
        return {
            "state": "APPROVAL_REJECTED",
            "detail": approval.decision_note or "A human declined the recommendation.",
            "recommendation_id": rec.recommendation_id,
            "approval_id": approval.approval_id,
        }
    if approval.status == "EXPIRED":
        return {
            "state": "APPROVAL_EXPIRED",
            "detail": "The approval window closed before a decision was made.",
            "recommendation_id": rec.recommendation_id,
            "approval_id": approval.approval_id,
        }
    return {
        "state": "APPROVED",
        "detail": "Approved. Execution has not yet been requested.",
        "recommendation_id": rec.recommendation_id,
        "approval_id": approval.approval_id,
    }


# ------------------------------------------------------------------------- incidents
@router.get("/incidents/{incident_id}")
def incident_detail(incident_id: int, session: Session = Depends(get_session)) -> dict:
    """Day 3 truth for one incident: detections, evidence, RCA, gateway health."""
    analysis_run_id = _analysis_run_for(session, incident_id)
    if analysis_run_id is None:
        raise not_found("analysis run for incident", incident_id)

    handoff = build_handoff(session, analysis_run_id).as_dict()

    try:
        world = load_world_state(session, incident_id)
        # `health` maps gateway_id -> a LIST of validity windows; `profiles` maps
        # gateway_id -> one steady-state profile. The window that matters is the one
        # covering the incident, so pick by overlap rather than taking the first.
        from aventum_counterfactual.simulator import p_success, runtime_profile_for

        gateways = []
        for gateway_id, windows in (world.health or {}).items():
            window = _window_covering(windows, world.window_start)
            profile = (world.profiles or {}).get(gateway_id)
            degraded = gateway_id == world.affected_gateway_id
            # The baseline health window is a year-long steady state and reads HEALTHY
            # even mid-incident; the incident's effect arrives through the multipliers.
            # `runtime_profile_for` is the engine's own combination of the two and
            # contains no arithmetic of its own, so the number shown on screen is the
            # same one the simulator reasons with rather than a second opinion computed
            # in the transport layer.
            runtime = runtime_profile_for(world, gateway_id, degraded)
            gateways.append(
                {
                    "gateway_id": gateway_id,
                    "baseline_health_state": window.health_state if window else None,
                    # What this gateway actually looks like UNDER the incident.
                    "effective_failure_probability": ser.num(
                        runtime.effective_failure_probability
                    ),
                    "effective_success_probability": ser.num(p_success(runtime)),
                    "baseline_failure_probability": (
                        ser.num(profile.baseline_failure_probability) if profile else None
                    ),
                    "baseline_traffic_weight": (
                        ser.num(profile.baseline_traffic_weight) if profile else None
                    ),
                    "is_affected": degraded,
                    "incident_failure_multiplier": (
                        ser.num(world.failure_multiplier) if degraded else None
                    ),
                    # Capacity telemetry does not exist anywhere in this system (§11).
                    "capacity": ser.UNAVAILABLE,
                    # Modelled infrastructure, never production telemetry.
                    "truth": ser.SYNTHETIC,
                }
            )
        gateways.sort(key=lambda g: g["gateway_id"])
        affected_gateway_id = world.affected_gateway_id
    except Exception:
        # Gateway health is contextual, not load-bearing for the incident view, so its
        # absence must not take the screen down. It IS logged: a silent except here once
        # hid a real shape bug, and an empty panel with no server-side trace is worse
        # than a broken one.
        log.exception("gateway health unavailable for incident %s", incident_id)
        gateways, affected_gateway_id = [], None

    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "analysis_run_id": analysis_run_id,
        # Historical transaction facts.
        "truth_note": {
            "incident": ser.SYNTHETIC,
            "detections": ser.OBSERVED,
            "evidence": ser.OBSERVED,
            "rca": ser.DETERMINISTIC,
            "gateway_health": ser.SYNTHETIC,
        },
        **handoff,
        "gateway_health": gateways,
        "affected_gateway_id": affected_gateway_id,
        "recovery": _recovery_state(session, incident_id),
    }


def _window_covering(windows: list, at):
    """The health window covering `at`, else the last one. Never invents a state."""
    if not windows:
        return None
    for w in windows:
        if w.valid_from <= at <= w.valid_to:
            return w
    return windows[-1]


def _analysis_run_for(session: Session, incident_id: int) -> int | None:
    return session.execute(
        text(
            "SELECT analysis_run_id FROM incident_analysis_runs "
            "WHERE incident_id = :i ORDER BY analysis_run_id DESC LIMIT 1"
        ),
        {"i": incident_id},
    ).scalar()


# ----------------------------------------------------------------------- simulations
@router.get("/incidents/{incident_id}/simulations")
def incident_simulations(incident_id: int, session: Session = Depends(get_session)) -> dict:
    sims = session.scalars(
        select(CounterfactualSimulation)
        .where(CounterfactualSimulation.incident_id == incident_id)
        .order_by(CounterfactualSimulation.simulation_id)
    ).all()
    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "incident_id": incident_id,
        "simulations": ser.simulation_list(sims),
    }


@router.get("/simulations/{simulation_id}")
def simulation_detail(simulation_id: int, session: Session = Depends(get_session)) -> dict:
    sim = session.get(CounterfactualSimulation, simulation_id)
    if sim is None:
        raise not_found("simulation", simulation_id)
    return {"environment": ser.ENVIRONMENT_NOTICE, **ser.simulation_list([sim])[0]}


@router.post("/incidents/{incident_id}/analyze")
def analyze(incident_id: int, session: Session = Depends(get_session)) -> dict:
    """
    Run the DETERMINISTIC decision pipeline: sweep candidates, apply policy, recommend.

    This is Day 4A and involves no agent. It is the path that must keep working when
    Qwen is unavailable (§27), which is why the agent has its own separate endpoint.
    """
    analysis_run_id = _analysis_run_for(session, incident_id)
    if analysis_run_id is None:
        raise not_found("analysis run for incident", incident_id)

    result = run_decision_pipeline(session, incident_id, analysis_run_id)
    _commit(session)
    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "incident_id": incident_id,
        "analysis_run_id": analysis_run_id,
        "recommendation": ser.recommendation_row(
            result.recommendation,
            decision={
                "permitted": result.decision.permitted,
                "reason_codes": getattr(result.decision, "reason_codes", None),
            },
        ),
        "requires_approval": result.requires_approval,
        "elapsed_ms": result.elapsed_ms,
    }


# -------------------------------------------------------------------- recommendation
@router.get("/incidents/{incident_id}/recommendation")
def incident_recommendation(incident_id: int, session: Session = Depends(get_session)) -> dict:
    """
    The persisted recommendation, its approval, its action and its verification.

    Returned as one object because they are one lifecycle, and because a UI that has to
    make four calls to decide what to render will eventually render an inconsistent
    combination of them.
    """
    rec = session.scalars(
        select(Recommendation)
        .where(Recommendation.incident_id == incident_id)
        .order_by(Recommendation.recommendation_id.desc())
    ).first()
    if rec is None:
        return {
            "environment": ser.ENVIRONMENT_NOTICE,
            "incident_id": incident_id,
            "recommendation": None,
            "approval": None,
            "action": None,
            "verification": None,
            "recovery": _recovery_state(session, incident_id),
        }

    approval = session.scalars(
        select(Approval)
        .where(Approval.recommendation_id == rec.recommendation_id)
        .order_by(Approval.approval_id.desc())
    ).first()
    action = session.scalars(
        select(Action)
        .where(Action.recommendation_id == rec.recommendation_id)
        .order_by(Action.action_id.desc())
    ).first()
    verification = get_verification(session, action.action_id) if action else None

    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "incident_id": incident_id,
        "recommendation": ser.recommendation_row(rec),
        # Staleness is RE-DERIVED, never read from a status column (§26).
        "stale": _staleness(session, rec),
        "approval": ser.approval_row(approval) if approval else None,
        "action": ser.action_row(action) if action else None,
        "verification": ser.verification_row(verification) if verification else None,
        "recovery": _recovery_state(session, incident_id),
    }


def _staleness(session: Session, rec: Recommendation) -> dict:
    """
    Is this recommendation still executable?

    Two independent ways it can go stale: the clock (expiry), and the world (the cited
    simulation's inputs no longer describe reality). Both are computed here rather than
    trusted from a column, because a stored `is_stale` flag is only as fresh as the last
    process that remembered to update it.
    """
    reasons: list[str] = []
    if is_expired(rec):
        reasons.append("The recommendation's validity window has passed.")

    sim = session.get(CounterfactualSimulation, rec.simulation_id)
    if sim is None:
        reasons.append("The cited simulation no longer exists.")
    else:
        try:
            world = load_world_state(session, rec.incident_id)
            from aventum_counterfactual.fingerprint import compute_input_fingerprint

            # Re-derive the fingerprint from the CURRENT world using the simulation's own
            # recorded seed and policy version. If the world has moved, the two differ.
            current = compute_input_fingerprint(
                world, sim.simulation_seed, sim.policy_version
            )
            if current != sim.input_fingerprint:
                reasons.append(
                    "The incident's inputs have changed since this candidate was simulated."
                )
        except Exception:
            # Inability to re-derive is not proof of staleness; say so rather than
            # guessing in either direction.
            reasons.append("UNAVAILABLE: staleness could not be re-derived.")

    return {
        "is_stale": bool(reasons) and "UNAVAILABLE" not in " ".join(reasons),
        "reasons": reasons,
        "next_step": (
            "Re-run analysis to produce a fresh simulation and recommendation."
            if reasons
            else None
        ),
    }


# ---------------------------------------------------------------------- approval
@router.post("/recommendations/{recommendation_id}/approval-request")
def create_approval_request(
    recommendation_id: int, session: Session = Depends(get_session)
) -> dict:
    """Ask a human. The API cannot answer on their behalf."""
    rec = session.get(Recommendation, recommendation_id)
    if rec is None:
        raise not_found("recommendation", recommendation_id)

    # Deliberately does NOT re-run the decision pipeline.
    #
    # An earlier version did, purely to obtain a `PolicyDecision` for the approval
    # payload's `gates` field. That was wrong twice over: it re-simulated all thirteen
    # candidates and emitted a duplicate SIMULATION_COMPLETED event for each, polluting
    # the audit trail; and re-deriving a decision at approval time risks minting a
    # recommendation that differs from the one the operator is looking at.
    #
    # `request_approval` reads `policy_validation_result` off the persisted row and
    # refuses a BLOCKED recommendation on its own, so the authorisation check is intact.
    # The gate detail the payload would have carried is already persisted on the
    # recommendation as `policy_reason_codes` and is served from there.
    try:
        approval = request_approval(session, rec)
    except ApprovalError as exc:
        raise conflict("APPROVAL_NOT_PERMITTED", str(exc)) from exc
    _commit(session)
    return {"environment": ser.ENVIRONMENT_NOTICE, "approval": ser.approval_row(approval)}


@router.post("/approvals/{approval_id}/decision")
def approval_decision(
    approval_id: int,
    payload: dict = Body(...),
    session: Session = Depends(get_session),
) -> dict:
    """
    Record a human decision.

    The frontend does not get to assert that something was approved -- it submits a
    decision and an identity, and the backend either persists it or refuses. `APPROVED`
    appears in a response only after the row is written (§16).
    """
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise not_found("approval", approval_id)

    decision = str(payload.get("decision", "")).upper()
    if decision not in ("APPROVED", "REJECTED"):
        raise bad_request(
            "INVALID_DECISION", "decision must be exactly 'APPROVED' or 'REJECTED'."
        )
    approver = str(payload.get("approver_identity") or "").strip()
    if not approver:
        # An approval with no attributable human is not an approval.
        raise bad_request(
            "APPROVER_REQUIRED", "approver_identity is required for any human decision."
        )

    try:
        decide_approval(
            session,
            approval,
            decision=decision,
            approver_identity=approver,
            note=payload.get("note"),
        )
    except ApprovalError as exc:
        raise conflict("APPROVAL_STATE_ERROR", str(exc)) from exc

    _commit(session)
    return {"environment": ser.ENVIRONMENT_NOTICE, "approval": ser.approval_row(approval)}


@router.post("/approvals/expire-stale")
def expire_stale(session: Session = Depends(get_session)) -> dict:
    """Sweep approvals whose window has closed. Deterministic and idempotent."""
    expired = expire_stale_approvals(session)
    _commit(session)
    return {"expired": expired}


# --------------------------------------------------------------------- execution
@router.post("/recommendations/{recommendation_id}/execute")
def execute(
    recommendation_id: int,
    payload: dict = Body(default={}),
    session: Session = Depends(get_session),
) -> dict:
    """
    Execute through the SimulatedRoutingAdapter, after full server-side revalidation.

    The browser cannot execute anything. It asks; `execute_action` re-checks approval,
    expiry, staleness, policy and idempotency, and may refuse. A refusal is a 200 with a
    REJECTED action, not an error -- the operator needs to see WHY it was refused, and
    that reason is persisted state, not an exception string.
    """
    rec = session.get(Recommendation, recommendation_id)
    if rec is None:
        raise not_found("recommendation", recommendation_id)

    approval = session.scalars(
        select(Approval)
        .where(
            Approval.recommendation_id == recommendation_id,
            Approval.status == "APPROVED",
        )
        .order_by(Approval.approval_id.desc())
    ).first()
    if approval is None:
        raise conflict(
            "NO_APPROVAL",
            "This recommendation has no granted approval. Execution requires one.",
        )

    world = load_world_state(session, rec.incident_id)
    from aventum_action.pipeline import primary_alert_role

    outcome = execute_action(
        session,
        recommendation_id=recommendation_id,
        approval_id=approval.approval_id,
        world=world,
        alert_role=primary_alert_role(session, rec.analysis_run_id),
        executed_by=str(payload.get("executed_by") or approval.approver_identity or "operator"),
    )
    _commit(session)
    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "action": ser.action_row(outcome.action) if outcome.action else None,
        "rejected": outcome.action.status == "REJECTED" if outcome.action else True,
    }


@router.get("/actions/{action_id}")
def action_detail(action_id: int, session: Session = Depends(get_session)) -> dict:
    action = session.get(Action, action_id)
    if action is None:
        raise not_found("action", action_id)
    verification = get_verification(session, action_id)
    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "action": ser.action_row(action),
        "verification": ser.verification_row(verification) if verification else None,
    }


# ------------------------------------------------------------------- verification
@router.post("/actions/{action_id}/verify")
def run_verification(action_id: int, session: Session = Depends(get_session)) -> dict:
    """
    Independent verification (§18).

    Idempotent by construction: `uq_verification_identity` means a second request for the
    same action under the same verifier returns the stored verdict rather than producing
    a second opinion.
    """
    if session.get(Action, action_id) is None:
        raise not_found("action", action_id)
    result = verify_action(session, action_id)
    _commit(session)
    return {"environment": ser.ENVIRONMENT_NOTICE, "verification": ser.verification_row(result)}


@router.get("/actions/{action_id}/verification")
def read_verification(action_id: int, session: Session = Depends(get_session)) -> dict:
    result = get_verification(session, action_id)
    if result is None:
        return {"environment": ser.ENVIRONMENT_NOTICE, "verification": None}
    return {"environment": ser.ENVIRONMENT_NOTICE, "verification": ser.verification_row(result)}


# ------------------------------------------------------------------------- batch
@router.get("/batch/recovery")
def batch_recovery(session: Session = Depends(get_session)) -> dict:
    """Population-level recovery measurement (§19). Counted, never estimated."""
    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "batch": build_batch_summary(session).as_dict(),
    }


# ------------------------------------------------------------------------- audit
@router.get("/incidents/{incident_id}/audit")
def incident_audit(incident_id: int, session: Session = Depends(get_session)) -> dict:
    events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.incident_id == incident_id)
        .order_by(AuditEvent.event_id)
    ).all()
    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "incident_id": incident_id,
        "events": [ser.audit_row(e) for e in events],
    }


# ------------------------------------------------------------------------- agent
@router.get("/incidents/{incident_id}/agent")
def incident_agent(incident_id: int, session: Session = Depends(get_session)) -> dict:
    """
    The most recent agent run for this incident, or an explicit absence.

    Never fabricates activity. If no run exists, `agent_run` is null and the UI says the
    agent has not run -- it does not invent a plausible sequence of tool calls.
    """
    from aventum_counterfactual.models import AgentRun, AgentToolCall

    run = session.scalars(
        select(AgentRun)
        .where(AgentRun.incident_id == incident_id)
        .order_by(AgentRun.agent_run_id.desc())
    ).first()
    if run is None:
        return {
            "environment": ser.ENVIRONMENT_NOTICE,
            "agent_run": None,
            "detail": "No agent run exists for this incident.",
        }

    calls = session.scalars(
        select(AgentToolCall)
        .where(AgentToolCall.agent_run_id == run.agent_run_id)
        .order_by(AgentToolCall.sequence)
    ).all()
    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "agent_run": ser.agent_run_row(run, [ser.tool_call_row(c) for c in calls]),
    }


@router.post("/incidents/{incident_id}/agent/analyze")
def agent_analyze(incident_id: int, session: Session = Depends(get_session)) -> dict:
    """
    Run the Day 4B agent.

    A 503 here is an honest, expected outcome, not a bug: when Ollama is down the
    deterministic endpoints continue to serve and the UI is required to say so rather
    than fabricate a rationale (§27).
    """
    if not _config.agent_enabled:
        raise ApiError(503, "AGENT_DISABLED", "The agent is disabled in this environment.")

    analysis_run_id = _analysis_run_for(session, incident_id)
    if analysis_run_id is None:
        raise not_found("analysis run for incident", incident_id)

    from aventum_agent.errors import AgentUnavailable
    from aventum_agent.service import analyze_incident

    try:
        analysis = analyze_incident(session, incident_id, analysis_run_id)
    except AgentUnavailable as exc:
        raise ApiError(
            503,
            "AGENT_UNAVAILABLE",
            "The agent is unavailable. Deterministic incident analysis remains available.",
            {"reason": str(exc)[:200]},
        ) from exc

    outcome = analysis.outcome
    _commit(session)
    return {
        "environment": ser.ENVIRONMENT_NOTICE,
        "agent_run_id": getattr(outcome, "agent_run_id", None),
        "status": outcome.status,
        "final_state": outcome.final_state,
        "recommendation_id": outcome.recommendation_id,
        "approval_id": outcome.approval_id,
        "truth": ser.AI_GENERATED,
    }


# -------------------------------------------------------------------------- demo
@router.post("/demo/reset")
def demo_reset(session: Session = Depends(get_session)) -> dict:
    """
    Restore the flagship demo to a clean, deterministic starting state (§30).

    Clears ONLY the Day 4/Day 5 workflow tables. Observed transactions, the synthetic
    baseline and the Day 3 incident analysis are never touched -- a reset that could
    alter the canonical dataset would be a reset nobody should run.
    """
    if not _config.demo_reset_enabled:
        raise ApiError(403, "DEMO_RESET_DISABLED", "Demo reset is disabled in this environment.")

    from aventum_api.demo import reset_demo_state

    report = reset_demo_state(session)
    _commit(session)
    return {"environment": ser.ENVIRONMENT_NOTICE, **report}


app.include_router(router)
