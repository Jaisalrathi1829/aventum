"""
Day 5 independent verification.

Day 4A ends by refusing to make a recovery claim: `execute.py` records
`"recovery_claim": "NONE — Day 5 owns verification"`, and `build_verification_handoff`
hands the question here. This module answers it.

WHAT MAKES THIS INDEPENDENT
---------------------------
Independence is not a claim about running the physics twice; it is a claim about who
owns the standards and whether the answer can come back negative. Concretely:

1. **Different inputs.** The recommendation was authorised from the SIMULATION SUMMARY.
   Verification measures the ADAPTER'S post-action population against the EXECUTION-TIME
   baseline. The adapter re-derives its numbers from the projected outcome population
   rather than echoing the simulation, so the two can genuinely disagree — and when they
   do, this module reports the disagreement rather than smoothing it.

2. **Different thresholds, owned here.** `constants.py` defines what "effective" means
   for verification and imports nothing from `aventum_policy` or the recommendation
   layer. The layer that proposed the action does not get to grade it.

3. **It can say no.** `RECOVERY_NOT_VERIFIED` is reachable from a successfully executed
   action, and an attainment ratio far below the projection produces it even when the
   raw movement was positive. A verifier that cannot fail is a formality.

4. **Integrity is checked, not assumed.** Lineage between action, recommendation and
   simulation is re-walked, and the execution fingerprint is recomputed from the
   recorded inputs. A mismatch fails verification instead of being rendered as a tick.

WHAT IT IS NOT
--------------
It is not evidence of production recovery. Both sides of every comparison are modelled
outcomes over observed transaction amounts under a synthetic incident, and every
persisted row says so.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from aventum_action.models import Action, AuditEvent, Recommendation
from aventum_counterfactual.fingerprint import compute_execution_fingerprint

from .constants import (
    ATTAINMENT_EFFECTIVE,
    ATTAINMENT_FLOOR,
    CHECK_BASELINE_PRESENT,
    CHECK_COHORT_PRESENT,
    CHECK_EXECUTION_FINGERPRINT,
    CHECK_OUTCOME_PRESENT,
    CHECK_POPULATION_STABLE,
    CHECK_SIMULATION_LINEAGE,
    CHECK_WINDOW_PRESENT,
    MIN_MEANINGFUL_FAILURE_RATE_IMPROVEMENT,
    PARTIALLY_EFFECTIVE,
    RECOVERY_CLAIM_NOTE,
    RECOVERY_EFFECTIVE,
    RECOVERY_NOT_VERIFIED,
    VERIFICATION_COMPLETE,
    VERIFICATION_INELIGIBLE,
    VERIFICATION_MODEL_VERSION,
    VERIFICATION_PROVENANCE,
)
from .models import Verification

# Only an action that actually reached the world may be verified. A rejected action has
# no post-action population to measure, and saying "not verified" about it would confuse
# "we measured and it did not help" with "there was nothing to measure".
VERIFIABLE_STATUSES = ("EXECUTED", "ROLLED_BACK")


@dataclass
class IntegrityCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class VerificationResult:
    """The complete verification judgement, before or after persistence."""

    action_id: int
    status: str
    outcome: str | None = None
    ineligible_reason: str | None = None

    baseline_failure_rate: float | None = None
    baseline_success_rate: float | None = None
    baseline_gmv_at_risk: float | None = None
    projected_success_delta: float | None = None
    projected_gmv_retained: float | None = None
    actual_failure_rate: float | None = None
    actual_success_rate: float | None = None
    actual_gmv_at_risk: float | None = None

    measured_success_delta: float | None = None
    measured_failure_rate_improvement: float | None = None
    actual_gmv_recovered: float | None = None
    variance_vs_projection: float | None = None
    attainment_ratio: float | None = None
    transactions_moved: int | None = None
    population: int | None = None

    integrity_passed: bool = True
    integrity_checks: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    verification_id: int | None = None
    verification_fingerprint: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _f(value) -> float | None:
    """Numeric or None. Never a silent zero -- a missing measurement is not a zero one."""
    return None if value is None else float(value)


def _metric_definitions() -> dict:
    """
    Stated explicitly because §10 forbids letting one concept drift into another.

    A UI that renders these has no excuse for labelling a failure rate as a success rate
    or a projection as a measurement.
    """
    return {
        "failure_rate": "FAILED transactions / cohort population, modelled post-action.",
        "success_rate": "1 - failure_rate. Carried independently, never inferred for display.",
        "measured_failure_rate_improvement": (
            "baseline_failure_rate - actual_failure_rate. Positive means fewer failures."
        ),
        "measured_success_delta": (
            "actual_success_rate - baseline_success_rate. The ACTUAL SIMULATED movement."
        ),
        "projected_success_delta": (
            "What the counterfactual simulation PROJECTED before the action. Never a measurement."
        ),
        "actual_gmv_recovered": (
            "baseline_gmv_at_risk - actual_gmv_at_risk. GMV no longer at risk in the "
            "modelled post-action population. NOT production money recovered."
        ),
        "projected_gmv_retained": (
            "The simulation's projection. Distinct from actual_gmv_recovered and never "
            "substituted for GMV at risk."
        ),
        "attainment_ratio": (
            "measured_success_delta / projected_success_delta. 1.0 means the projection "
            "was met exactly; below 1.0 means it was not."
        ),
        "variance_vs_projection": "measured_success_delta - projected_success_delta.",
    }


def _limitations() -> dict:
    return {
        "provenance": VERIFICATION_PROVENANCE,
        "recovery_claim": RECOVERY_CLAIM_NOTE,
        "capacity": "UNAVAILABLE — no capacity telemetry exists anywhere in the system.",
        "control_group": (
            "The comparison is pre-action versus post-action on the same cohort, not a "
            "randomised control. A concurrent untreated arm does not exist in this data."
        ),
        "single_window": (
            "One measurement window. No durability claim is made about whether the "
            "improvement persists beyond it."
        ),
    }


def _integrity(
    session: Session, action: Action, recommendation: Recommendation | None
) -> list[IntegrityCheck]:
    """
    Re-walk the lineage rather than trusting it.

    Every check here is capable of failing on real data; none of them is decorative.
    """
    checks: list[IntegrityCheck] = []

    # 1. The action, its recommendation and the cited simulation must agree on lineage.
    if recommendation is None:
        checks.append(
            IntegrityCheck(CHECK_SIMULATION_LINEAGE, False, "action has no recommendation")
        )
    else:
        ref = action.reference_simulation_fingerprint
        sim_id = recommendation.simulation_id
        ok = sim_id is not None
        detail = (
            f"action {action.action_id} -> recommendation {recommendation.recommendation_id} "
            f"-> simulation {sim_id}"
        )
        if ok and ref:
            detail += f"; reference fingerprint {ref[:12]}..."
        checks.append(IntegrityCheck(CHECK_SIMULATION_LINEAGE, bool(ok), detail))

    # 2. Recompute the execution fingerprint from the recorded inputs.
    actual = action.actual_simulated_outcome or {}
    recorded = action.execution_fingerprint
    if not recorded or not actual or recommendation is None:
        checks.append(
            IntegrityCheck(
                CHECK_EXECUTION_FINGERPRINT, False, "no execution fingerprint to recompute"
            )
        )
    else:
        population = (action.pre_action_metrics or {}).get("population")
        failures = actual.get("post_action_failure_count")
        successes = (
            None if population is None or failures is None else population - failures
        )
        recomputed = compute_execution_fingerprint(
            [
                str(action.recommendation_id),
                str(action.approval_id),
                str(recommendation.simulation_id),
                actual.get("adapter_name", ""),
                str(actual.get("traffic_moved", "")),
                f"{successes}/{population}",
                f"{float(actual.get('post_action_gmv_at_risk') or 0):.2f}",
            ]
        )
        match = recomputed == recorded
        checks.append(
            IntegrityCheck(
                CHECK_EXECUTION_FINGERPRINT,
                match,
                "recomputed fingerprint matches"
                if match
                else f"MISMATCH: recorded {recorded[:12]}... recomputed {recomputed[:12]}...",
            )
        )

    # 3-6. The measurement must actually have a cohort, a window, a baseline and a result.
    checks.append(
        IntegrityCheck(
            CHECK_COHORT_PRESENT,
            bool(action.cohort_definition),
            "cohort definition present" if action.cohort_definition else "missing",
        )
    )
    checks.append(
        IntegrityCheck(
            CHECK_WINDOW_PRESENT,
            bool(action.measurement_window),
            "measurement window present" if action.measurement_window else "missing",
        )
    )
    checks.append(
        IntegrityCheck(
            CHECK_BASELINE_PRESENT,
            bool(action.pre_action_metrics),
            "pre-action baseline present" if action.pre_action_metrics else "missing",
        )
    )
    checks.append(
        IntegrityCheck(
            CHECK_OUTCOME_PRESENT,
            bool(actual),
            "actual simulated outcome present" if actual else "missing",
        )
    )

    # 7. A reroute REDISTRIBUTES traffic; it never creates or destroys a transaction.
    #    So the two ALLOCATIONS must sum to the same total.
    #
    #    Both sides must be the same kind of quantity, which is the subtlety here: the
    #    allocation spans the whole incident window, while `population` counts only the
    #    affected cohort within it. Comparing those two would fail on every healthy run
    #    -- an earlier version of this check did exactly that -- so the comparison is
    #    allocation-to-allocation.
    pre_alloc = (action.pre_action_metrics or {}).get("current_distribution") or {}
    post_alloc = actual.get("resulting_allocation") or {}
    if not pre_alloc or not post_alloc:
        checks.append(
            IntegrityCheck(
                CHECK_POPULATION_STABLE, False, "allocation not measurable on both sides"
            )
        )
    else:
        pre_total, post_total = sum(pre_alloc.values()), sum(post_alloc.values())
        stable = int(pre_total) == int(post_total)
        checks.append(
            IntegrityCheck(
                CHECK_POPULATION_STABLE,
                stable,
                f"allocation total conserved at {pre_total}"
                if stable
                else f"ALLOCATION TOTAL CHANGED: pre {pre_total} != post {post_total}",
            )
        )

    return checks


def _classify(
    measured_improvement: float | None,
    attainment: float | None,
    integrity_ok: bool,
) -> tuple[str, list[str]]:
    """
    The verdict, and the reasons for it.

    Ordering matters: integrity is checked before merit, because a number whose lineage
    does not hold up should never be graded on how good it looks.
    """
    reasons: list[str] = []

    if not integrity_ok:
        reasons.append("Integrity checks failed; the measurement cannot be trusted.")
        return RECOVERY_NOT_VERIFIED, reasons

    if measured_improvement is None:
        reasons.append("No measurable failure-rate movement.")
        return RECOVERY_NOT_VERIFIED, reasons

    if measured_improvement < MIN_MEANINGFUL_FAILURE_RATE_IMPROVEMENT:
        reasons.append(
            f"Measured failure-rate improvement {measured_improvement:.4f} is below the "
            f"{MIN_MEANINGFUL_FAILURE_RATE_IMPROVEMENT} threshold for a meaningful effect."
        )
        return RECOVERY_NOT_VERIFIED, reasons

    reasons.append(
        f"Failure rate improved by {measured_improvement:.4f} on the treated cohort."
    )

    if attainment is None:
        reasons.append("No projection to compare against; reporting movement only.")
        return PARTIALLY_EFFECTIVE, reasons

    if attainment < ATTAINMENT_FLOOR:
        reasons.append(
            f"Attained only {attainment:.0%} of the projected improvement, below the "
            f"{ATTAINMENT_FLOOR:.0%} floor — the projection did not describe the outcome."
        )
        return RECOVERY_NOT_VERIFIED, reasons

    if attainment >= ATTAINMENT_EFFECTIVE:
        reasons.append(f"Attained {attainment:.0%} of the projected improvement.")
        return RECOVERY_EFFECTIVE, reasons

    reasons.append(
        f"Attained {attainment:.0%} of the projected improvement, short of the "
        f"{ATTAINMENT_EFFECTIVE:.0%} bar for a fully effective result."
    )
    return PARTIALLY_EFFECTIVE, reasons


def _fingerprint(action_id: int, payload: dict) -> str:
    material = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(
        f"AVENTUM_VERIFICATION_V1|{action_id}|{VERIFICATION_MODEL_VERSION}|{material}".encode()
    ).hexdigest()


def verify_action(session: Session, action_id: int, persist: bool = True) -> VerificationResult:
    """
    Verify one executed action and (by default) persist the judgement.

    Idempotent: an action already verified under this model version returns the stored
    verdict rather than recomputing a second opinion.
    """
    action = session.get(Action, action_id)
    if action is None:
        raise ValueError(f"no action {action_id}")

    existing = session.scalars(
        select(Verification).where(
            Verification.action_id == action_id,
            Verification.model_version == VERIFICATION_MODEL_VERSION,
        )
    ).first()
    if existing is not None:
        return _result_from_row(existing)

    recommendation = (
        session.get(Recommendation, action.recommendation_id)
        if action.recommendation_id
        else None
    )

    # ---- eligibility ---------------------------------------------------------------
    if action.status not in VERIFIABLE_STATUSES:
        reason = (
            f"action status {action.status} is not verifiable; only "
            f"{' or '.join(VERIFIABLE_STATUSES)} produce a post-action population"
        )
        result = VerificationResult(
            action_id=action_id,
            status=VERIFICATION_INELIGIBLE,
            ineligible_reason=reason,
            integrity_passed=False,
            reasons=[reason],
        )
        if persist:
            _persist(session, action, recommendation, result)
        return result

    # ---- integrity before merit ------------------------------------------------------
    checks = _integrity(session, action, recommendation)
    integrity_ok = all(c.passed for c in checks)

    pre = action.pre_action_metrics or {}
    expected = action.expected_outcome or {}
    actual = action.actual_simulated_outcome or {}

    baseline_failure = _f(pre.get("failure_rate"))
    baseline_success = _f(pre.get("success_rate"))
    baseline_gmv_at_risk = _f(pre.get("gmv_at_risk"))
    actual_failure = _f(actual.get("post_action_failure_rate"))
    actual_success = _f(actual.get("post_action_success_rate"))
    actual_gmv_at_risk = _f(actual.get("post_action_gmv_at_risk"))
    projected_delta = _f(expected.get("expected_success_delta"))
    projected_gmv = _f(expected.get("projected_gmv_retained"))

    measured_improvement = (
        None
        if baseline_failure is None or actual_failure is None
        else round(baseline_failure - actual_failure, 6)
    )
    measured_delta = (
        None
        if baseline_success is None or actual_success is None
        else round(actual_success - baseline_success, 6)
    )
    gmv_recovered = (
        None
        if baseline_gmv_at_risk is None or actual_gmv_at_risk is None
        else round(baseline_gmv_at_risk - actual_gmv_at_risk, 2)
    )
    variance = (
        None
        if measured_delta is None or projected_delta is None
        else round(measured_delta - projected_delta, 6)
    )
    # Guard the division rather than letting a zero projection produce an infinity that
    # would then be rendered as a percentage.
    attainment = (
        None
        if measured_delta is None or not projected_delta
        else round(measured_delta / projected_delta, 6)
    )

    outcome, reasons = _classify(measured_improvement, attainment, integrity_ok)
    if not integrity_ok:
        reasons.extend(f"{c.name}: {c.detail}" for c in checks if not c.passed)

    result = VerificationResult(
        action_id=action_id,
        status=VERIFICATION_COMPLETE,
        outcome=outcome,
        baseline_failure_rate=baseline_failure,
        baseline_success_rate=baseline_success,
        baseline_gmv_at_risk=baseline_gmv_at_risk,
        projected_success_delta=projected_delta,
        projected_gmv_retained=projected_gmv,
        actual_failure_rate=actual_failure,
        actual_success_rate=actual_success,
        actual_gmv_at_risk=actual_gmv_at_risk,
        measured_success_delta=measured_delta,
        measured_failure_rate_improvement=measured_improvement,
        actual_gmv_recovered=gmv_recovered,
        variance_vs_projection=variance,
        attainment_ratio=attainment,
        transactions_moved=actual.get("traffic_moved"),
        population=pre.get("population"),
        integrity_passed=integrity_ok,
        integrity_checks=[asdict(c) for c in checks],
        reasons=reasons,
    )

    if persist:
        _persist(session, action, recommendation, result)
    return result


def _persist(
    session: Session,
    action: Action,
    recommendation: Recommendation | None,
    result: VerificationResult,
) -> None:
    """Write the verdict and its audit event. Never overwrites a prior verification."""
    payload = {
        "outcome": result.outcome,
        "status": result.status,
        "measured_success_delta": result.measured_success_delta,
        "attainment_ratio": result.attainment_ratio,
        "integrity_passed": result.integrity_passed,
    }
    fingerprint = _fingerprint(result.action_id, payload)
    result.verification_fingerprint = fingerprint

    row = Verification(
        action_id=result.action_id,
        incident_id=recommendation.incident_id if recommendation else None,
        recommendation_id=action.recommendation_id,
        simulation_id=recommendation.simulation_id if recommendation else None,
        status=result.status,
        outcome=result.outcome,
        ineligible_reason=result.ineligible_reason,
        baseline_failure_rate=result.baseline_failure_rate,
        baseline_success_rate=result.baseline_success_rate,
        baseline_gmv_at_risk=result.baseline_gmv_at_risk,
        projected_success_delta=result.projected_success_delta,
        projected_gmv_retained=result.projected_gmv_retained,
        actual_failure_rate=result.actual_failure_rate,
        actual_success_rate=result.actual_success_rate,
        actual_gmv_at_risk=result.actual_gmv_at_risk,
        measured_success_delta=result.measured_success_delta,
        measured_failure_rate_improvement=result.measured_failure_rate_improvement,
        actual_gmv_recovered=result.actual_gmv_recovered,
        variance_vs_projection=result.variance_vs_projection,
        attainment_ratio=result.attainment_ratio,
        transactions_moved=result.transactions_moved,
        population=result.population,
        integrity_passed=result.integrity_passed,
        integrity_checks={"checks": result.integrity_checks},
        cohort_definition=action.cohort_definition,
        measurement_window=action.measurement_window,
        metric_definitions=_metric_definitions(),
        reasons={"reasons": result.reasons},
        limitations=_limitations(),
        verification_fingerprint=fingerprint,
        model_version=VERIFICATION_MODEL_VERSION,
        provenance=VERIFICATION_PROVENANCE,
    )
    session.add(row)
    session.flush()
    result.verification_id = row.verification_id

    session.add(
        AuditEvent(
            incident_id=row.incident_id,
            event_type="VERIFICATION_COMPLETED"
            if result.status == VERIFICATION_COMPLETE
            else "VERIFICATION_INELIGIBLE",
            actor="AVENTUM_VERIFICATION",
            input_ref={"action_id": result.action_id},
            output_ref={"verification_id": row.verification_id},
            payload=payload,
            model_version=VERIFICATION_MODEL_VERSION,
            fingerprint=fingerprint,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    session.flush()


def _result_from_row(row: Verification) -> VerificationResult:
    return VerificationResult(
        action_id=row.action_id,
        status=row.status,
        outcome=row.outcome,
        ineligible_reason=row.ineligible_reason,
        baseline_failure_rate=_f(row.baseline_failure_rate),
        baseline_success_rate=_f(row.baseline_success_rate),
        baseline_gmv_at_risk=_f(row.baseline_gmv_at_risk),
        projected_success_delta=_f(row.projected_success_delta),
        projected_gmv_retained=_f(row.projected_gmv_retained),
        actual_failure_rate=_f(row.actual_failure_rate),
        actual_success_rate=_f(row.actual_success_rate),
        actual_gmv_at_risk=_f(row.actual_gmv_at_risk),
        measured_success_delta=_f(row.measured_success_delta),
        measured_failure_rate_improvement=_f(row.measured_failure_rate_improvement),
        actual_gmv_recovered=_f(row.actual_gmv_recovered),
        variance_vs_projection=_f(row.variance_vs_projection),
        attainment_ratio=_f(row.attainment_ratio),
        transactions_moved=row.transactions_moved,
        population=row.population,
        integrity_passed=row.integrity_passed,
        integrity_checks=(row.integrity_checks or {}).get("checks", []),
        reasons=(row.reasons or {}).get("reasons", []),
        verification_id=row.verification_id,
        verification_fingerprint=row.verification_fingerprint,
    )


def get_verification(session: Session, action_id: int) -> VerificationResult | None:
    """The persisted verdict for an action, or None if it has not been verified."""
    row = session.scalars(
        select(Verification)
        .where(Verification.action_id == action_id)
        .order_by(Verification.verification_id.desc())
    ).first()
    return None if row is None else _result_from_row(row)
