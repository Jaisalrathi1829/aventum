"""
Approach B simulated-outcome generation.

THE CENTRAL RULE
----------------
A degradation ADDS modelled failures to the affected cohort. It never moves, rescues,
or reallocates an observed failure.

    observed FAILED  -> simulated FAILED        (always; never rescued)
    observed SUCCESS -> simulated FAILED w.p. p_add, else SUCCESS

The rejected Approach A held the failure total fixed and redistributed it, which was
measured to drop the healthy control group to ~0.48x its baseline rate at 25% severity
-- healthy gateways appearing to get *healthier* during an incident, which no real
degradation does and which would hand RCA an artificially easy contrast.

WHERE p_add COMES FROM
----------------------
The incident's `failure_multiplier` scales the gateway's Day 2B baseline failure
probability through `GatewayRuntimeProfile.effective_failure_probability`, exactly the
lever Day 2B reserved for this. Converting that target into a probability applied only
to observed successes:

    p_eff  = clamp(p_base * failure_multiplier)
    p_add  = (p_eff - p_base) / (1 - p_base)

so the realised cohort rate lands at approximately

    observed_rate + (1 - observed_rate) * p_add  ~=  p_eff

while every observed failure stays exactly where history put it.

COHERENCE
---------
Status, response code, latency regime, and latency value are never drawn independently.
Once the simulated status is known, the row is completed by Day 2B's own
`generate_signals()` funnel using a degraded `GatewayRuntimeProfile`, so a degraded
health state raises failures, lengthens latency, and tilts the response mix toward
infrastructure-side codes together, through one state change.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from aventum_synth.outcome_model import GatewayRuntimeProfile, generate_signals

from . import INCIDENT_CONFIG_VERSION, INCIDENT_MODEL_VERSION
from .models import Incident, IncidentSimulationRun, SimulatedIncidentOutcome
from .rng import LANE_OUTCOME, incident_digest_for, lane_uniform

# Health state a degraded cohort is modelled as being in. Day 2B already defined
# DEGRADED in the health vocabulary specifically so Day 3 needs no migration for it.
DEGRADED_HEALTH_STATE = "DEGRADED"
HEALTHY_HEALTH_STATE = "HEALTHY"


@dataclass(frozen=True)
class SimulationResult:
    simulation_run_id: int
    incident_id: int
    rows_in_window: int
    rows_simulated: int
    rows_changed: int
    simulation_fingerprint: str
    affected_population: int
    control_population: int
    affected_failure_rate: float
    control_failure_rate: float
    elapsed_ms: float


# One set-based query for the whole window. Every field the generator needs travels with
# it, so nothing inside the per-row loop touches the database (no N+1).
_WINDOW_SQL = text(
    """
    SELECT
        t.transaction_id,
        t.status                         AS observed_status,
        t.amount,
        t.timestamp,
        t.sender_bank,
        t.payment_method,
        t.region,
        t.device,
        t.network,
        a.selected_gateway_id            AS gateway_id,
        a.gateway_latency_ms             AS baseline_latency_ms,
        a.latency_regime                 AS baseline_latency_regime,
        a.gateway_response_code          AS baseline_response_code,
        a.response_attribution           AS baseline_response_attribution,
        a.modeled_failure_probability    AS baseline_modeled_failure_probability,
        a.source_ingestion_run_id,
        a.generation_run_id
    FROM transactions t
    JOIN synthetic_infrastructure_assignments a
      ON a.transaction_id = t.transaction_id
    WHERE a.generation_run_id = :generation_run_id
      AND t.timestamp >= :window_start
      AND t.timestamp <  :window_end
    ORDER BY t.transaction_id
    """
)

# Day 2B profile parameters, read once per run.
_PROFILE_SQL = text(
    """
    SELECT gateway_id,
           profile_version,
           baseline_failure_probability,
           latency_multiplier,
           failure_response_mix
    FROM synthetic_gateway_profiles
    WHERE profile_version = :profile_version
    """
)


def _load_profiles(session: Session, profile_version: str = "baseline-v1") -> dict[str, dict]:
    rows = session.execute(_PROFILE_SQL, {"profile_version": profile_version}).mappings().all()
    if not rows:
        raise RuntimeError(
            f"no Day 2B gateway profiles found for profile_version={profile_version!r}; "
            "run `python -m aventum_synth.cli generate` first"
        )
    return {row["gateway_id"]: dict(row) for row in rows}


def is_in_affected_cohort(row: dict, incident: Incident) -> bool:
    """
    Whether one transaction falls inside the incident's affected population.

    Generalised deliberately: a gateway incident narrows on gateway, an issuer incident
    narrows on sender_bank across every gateway, a systemic incident narrows on nothing.
    The same code path therefore serves all incident types, which is what stops the
    simulator from being a gateway_C special case.
    """
    if incident.affected_gateway_id is not None:
        if row["gateway_id"] != incident.affected_gateway_id:
            return False

    segment = incident.affected_segment or {}
    for key, expected in segment.items():
        if row.get(key) != expected:
            return False
    return True


def build_runtime_profile(
    gateway_profile: dict,
    incident: Incident,
    degraded: bool,
) -> GatewayRuntimeProfile:
    """
    Build the Day 2B runtime profile for one row, degraded or not.

    All incident influence enters here and nowhere else. That is the single funnel the
    coherence requirement depends on: downstream code cannot raise the failure rate
    without also getting the matching latency and response-mix shift.
    """
    return GatewayRuntimeProfile(
        gateway_id=gateway_profile["gateway_id"],
        profile_version=gateway_profile["profile_version"],
        baseline_failure_probability=float(gateway_profile["baseline_failure_probability"]),
        latency_multiplier=float(gateway_profile["latency_multiplier"]),
        failure_response_mix={
            k: float(v) for k, v in dict(gateway_profile["failure_response_mix"]).items()
        },
        health_state=DEGRADED_HEALTH_STATE if degraded else HEALTHY_HEALTH_STATE,
        failure_multiplier=float(incident.failure_multiplier) if degraded else 1.0,
        health_latency_multiplier=float(incident.latency_multiplier) if degraded else 1.0,
        timeout_multiplier=float(incident.timeout_multiplier) if degraded else 1.0,
    )


def added_failure_probability(profile: GatewayRuntimeProfile) -> float:
    """
    The probability an observed SUCCESS is modelled as failing under the degradation.

    Derived so the resulting cohort rate approaches the profile's effective failure
    probability while leaving every observed failure untouched. Returns 0 for a healthy
    profile, which is why control rows are provably unchanged rather than
    coincidentally unchanged.
    """
    p_base = profile.baseline_failure_probability
    p_eff = profile.effective_failure_probability
    if p_eff <= p_base:
        return 0.0
    denominator = 1.0 - p_base
    if denominator <= 0:
        return 0.0
    return min(max((p_eff - p_base) / denominator, 0.0), 1.0)


def _fingerprint_rows(rendered: list[str]) -> str:
    """SHA-256 over the ordered, deterministically rendered simulated rows."""
    digest = hashlib.sha256()
    for line in rendered:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def simulate_incident(
    session: Session,
    incident: Incident,
    simulation_seed: str | None = None,
    profile_version: str = "baseline-v1",
) -> SimulationResult:
    """
    Generate the Approach B simulated-outcome layer for one incident.

    Idempotent by replacement: any prior simulation for this incident is deleted first,
    so re-running converges on the same state rather than accumulating duplicate rows.
    `transactions` is only ever read.
    """
    started = time.perf_counter()
    seed = simulation_seed or incident.incident_seed

    run = IncidentSimulationRun(
        incident_id=incident.incident_id,
        simulation_seed=seed,
        incident_model_version=INCIDENT_MODEL_VERSION,
        incident_config_version=INCIDENT_CONFIG_VERSION,
        status="RUNNING",
        model_parameters={
            "failure_multiplier": float(incident.failure_multiplier),
            "latency_multiplier": float(incident.latency_multiplier),
            "timeout_multiplier": float(incident.timeout_multiplier),
            "affected_gateway_id": incident.affected_gateway_id,
            "affected_segment": incident.affected_segment,
            "profile_version": profile_version,
            "approach": "B (additive; observed failures never reallocated)",
        },
    )
    session.add(run)
    session.flush()

    # Replace any earlier simulation of this incident.
    session.execute(
        delete(SimulatedIncidentOutcome).where(
            SimulatedIncidentOutcome.incident_id == incident.incident_id,
            SimulatedIncidentOutcome.simulation_run_id != run.simulation_run_id,
        )
    )
    session.execute(
        delete(IncidentSimulationRun).where(
            IncidentSimulationRun.incident_id == incident.incident_id,
            IncidentSimulationRun.simulation_run_id != run.simulation_run_id,
        )
    )

    profiles = _load_profiles(session, profile_version)

    rows = (
        session.execute(
            _WINDOW_SQL,
            {
                "generation_run_id": incident.generation_run_id,
                "window_start": incident.incident_start,
                "window_end": incident.incident_end,
            },
        )
        .mappings()
        .all()
    )

    payload: list[dict] = []
    rendered: list[str] = []
    rows_changed = 0
    affected_total = 0
    affected_failed = 0
    control_total = 0
    control_failed = 0

    for row in rows:
        row = dict(row)
        gateway_id = row["gateway_id"]
        gateway_profile = profiles[gateway_id]
        degraded = is_in_affected_cohort(row, incident)

        runtime = build_runtime_profile(gateway_profile, incident, degraded)
        observed_status = row["observed_status"]

        if not degraded:
            # Control rows carry their Day 2B signals through unchanged. Regenerating
            # them would introduce variation the incident did not cause, and would make
            # "the control group is stable" an approximate claim instead of an exact one.
            simulated_status = observed_status
            response_code = row["baseline_response_code"]
            attribution = row["baseline_response_attribution"]
            latency_regime = row["baseline_latency_regime"]
            latency_ms = float(row["baseline_latency_ms"])
            modeled_probability = float(row["baseline_modeled_failure_probability"])
        else:
            digest = incident_digest_for(
                transaction_id=row["transaction_id"],
                incident_key=incident.incident_key,
                simulation_seed=seed,
                incident_model_version=INCIDENT_MODEL_VERSION,
                incident_config_version=INCIDENT_CONFIG_VERSION,
            )

            if observed_status == "FAILED":
                # Approach B: an observed failure is never rescued.
                simulated_status = "FAILED"
            else:
                p_add = added_failure_probability(runtime)
                uniform = lane_uniform(digest, LANE_OUTCOME)
                simulated_status = "FAILED" if uniform < p_add else "SUCCESS"

            # One funnel: status -> response family -> latency regime -> latency value.
            signals = generate_signals(digest, simulated_status, runtime)
            response_code = signals["gateway_response_code"]
            attribution = signals["response_attribution"]
            latency_regime = signals["latency_regime"]
            latency_ms = float(signals["gateway_latency_ms"])
            modeled_probability = float(signals["modeled_failure_probability"])

        changed = simulated_status != observed_status
        rows_changed += int(changed)

        if degraded:
            affected_total += 1
            affected_failed += int(simulated_status == "FAILED")
        else:
            control_total += 1
            control_failed += int(simulated_status == "FAILED")

        payload.append(
            {
                "incident_id": incident.incident_id,
                "simulation_run_id": run.simulation_run_id,
                "transaction_id": row["transaction_id"],
                "gateway_id": gateway_id,
                "observed_status": observed_status,
                "simulated_status": simulated_status,
                "simulated_response_code": response_code,
                "simulated_response_attribution": attribution,
                "simulated_latency_regime": latency_regime,
                "simulated_latency_ms": round(latency_ms, 2),
                "outcome_changed": changed,
                "modeled_failure_probability": round(modeled_probability, 6),
                "in_affected_cohort": degraded,
                "source_ingestion_run_id": row["source_ingestion_run_id"],
                "generation_run_id": row["generation_run_id"],
            }
        )
        # Rows arrive ordered by transaction_id, so this rendering is stable.
        rendered.append(
            f"{row['transaction_id']}|{observed_status}|{simulated_status}|"
            f"{response_code}|{latency_regime}|{latency_ms:.2f}|{int(degraded)}"
        )

    if payload:
        session.bulk_insert_mappings(SimulatedIncidentOutcome, payload)

    fingerprint = _fingerprint_rows(rendered)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    run.status = "SUCCEEDED"
    run.rows_in_window = len(rows)
    run.rows_simulated = len(payload)
    run.rows_changed = rows_changed
    run.simulation_fingerprint = fingerprint
    run.finished_at = datetime.now(timezone.utc)
    session.flush()

    return SimulationResult(
        simulation_run_id=run.simulation_run_id,
        incident_id=incident.incident_id,
        rows_in_window=len(rows),
        rows_simulated=len(payload),
        rows_changed=rows_changed,
        simulation_fingerprint=fingerprint,
        affected_population=affected_total,
        control_population=control_total,
        affected_failure_rate=(affected_failed / affected_total) if affected_total else 0.0,
        control_failure_rate=(control_failed / control_total) if control_total else 0.0,
        elapsed_ms=elapsed_ms,
    )


def latest_simulation_run(session: Session, incident_id: int) -> IncidentSimulationRun | None:
    return session.scalar(
        select(IncidentSimulationRun)
        .where(IncidentSimulationRun.incident_id == incident_id)
        .order_by(IncidentSimulationRun.simulation_run_id.desc())
        .limit(1)
    )
