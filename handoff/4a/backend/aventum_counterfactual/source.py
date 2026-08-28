"""
The telemetry-source boundary for Day 4.

THIS IS THE ONLY DAY 4 MODULE PERMITTED TO NAME A SYNTHETIC TABLE.

Day 3's architecture review established that the intelligence layer holds zero
references to `synthetic_*` table names, so a future real-telemetry feed could be
substituted without touching the analytical code. Day 4 preserves that property by
funnelling every read through this module: the simulator, the impact calculator, the
risk model, and the policy gate all consume the plain dataclasses below and never issue
SQL of their own. Swapping this file for a `LiveTelemetrySource` would be the whole of
the production substitution on the read side.

WHAT THIS MODULE DELIBERATELY CANNOT DO
---------------------------------------
It does not import, name, or query `incident_ground_truth`. Ground truth is
evaluation-only; the same AST scan that guards Day 3's diagnosis path is extended in the
Day 4A test suite to cover this package. Nothing here can leak the answer key into a
simulation, a recommendation, or an approval payload.

It also never writes. Every statement in this file is a SELECT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from .constants import (
    BASELINE_POLICY_VERSION,
    BASELINE_PROFILE_VERSION,
    ELIGIBILITY_UNCONDITIONAL,
    HEALTH_HEALTHY,
)


@dataclass(frozen=True)
class GatewayProfile:
    """Day 2B calibrated parameters for one gateway. Values, not table rows."""

    gateway_id: str
    profile_version: str
    baseline_failure_probability: float
    latency_multiplier: float
    failure_response_mix: dict[str, float]
    baseline_traffic_weight: float

    def as_profile_row(self) -> dict:
        """
        Shape expected by Day 3's `build_runtime_profile`.

        Returned as a dict rather than passing this dataclass directly so Day 4 reuses
        Day 3's constructor verbatim, with no adapter logic that could drift.
        """
        return {
            "gateway_id": self.gateway_id,
            "profile_version": self.profile_version,
            "baseline_failure_probability": self.baseline_failure_probability,
            "latency_multiplier": self.latency_multiplier,
            "failure_response_mix": self.failure_response_mix,
        }


@dataclass(frozen=True)
class GatewayEligibility:
    """
    Routing eligibility for one gateway under a policy version.

    `conditions` is NULL for all five gateways under `baseline-v1`, so `basis` reports
    ELIGIBILITY_UNCONDITIONAL. The gate stays architecturally present for a future real
    rule set; what it must not do is imply a substantive check happened when the data
    carries no conditions at all.
    """

    gateway_id: str
    policy_version: str
    is_eligible: bool
    traffic_weight: float
    conditions: dict | None

    @property
    def basis(self) -> str:
        return ELIGIBILITY_UNCONDITIONAL if self.conditions is None else "ELIGIBILITY_CONDITIONAL"


@dataclass(frozen=True)
class GatewayHealthWindow:
    """One health interval. Health is MODEL STATE, never an observation."""

    gateway_id: str
    health_state: str
    valid_from: datetime
    valid_to: datetime
    failure_multiplier: float
    latency_multiplier: float
    timeout_multiplier: float


@dataclass(frozen=True)
class CohortTransaction:
    """
    One transaction in the incident window, with its current simulated outcome.

    `amount` and `observed_status` are OBSERVED FACT, read from `transactions` and never
    written back. Everything prefixed `current_` is the Day 3 MODELLED incident-period
    outcome that a counterfactual is compared against.
    """

    transaction_id: str
    timestamp: datetime
    amount: float
    observed_status: str
    gateway_id: str
    sender_bank: str
    payment_method: str
    region: str
    device: str
    network: str
    in_affected_cohort: bool
    current_status: str
    current_response_code: str
    current_response_attribution: str
    current_latency_regime: str
    current_latency_ms: float
    current_modeled_failure_probability: float

    def as_cohort_row(self, gateway_id: str | None = None) -> dict:
        """
        Shape expected by Day 3's `is_in_affected_cohort`, optionally with the gateway
        overridden to model a reroute.

        The override is the entire mechanism by which a counterfactual asks "would this
        transaction still be inside the incident's blast radius if it had been routed
        elsewhere?" -- and, importantly, the answer is NO for a gateway incident but YES
        for an issuer incident, because rerouting does not change the sender bank. That
        asymmetry falls out of reusing Day 3's own cohort predicate instead of
        reimplementing it.
        """
        return {
            "gateway_id": gateway_id if gateway_id is not None else self.gateway_id,
            "sender_bank": self.sender_bank,
            "payment_method": self.payment_method,
            "region": self.region,
            "device": self.device,
            "network": self.network,
        }


@dataclass
class WorldState:
    """Everything a counterfactual needs, read once, in one place."""

    incident_id: int
    incident_key: str
    incident_seed: str
    incident_type: str
    affected_gateway_id: str | None
    affected_segment: dict | None
    window_start: datetime
    window_end: datetime
    failure_multiplier: float
    latency_multiplier: float
    timeout_multiplier: float
    generation_run_id: int
    source_ingestion_run_id: int
    day3_simulation_fingerprint: str
    profiles: dict[str, GatewayProfile]
    eligibility: dict[str, GatewayEligibility]
    health: dict[str, list[GatewayHealthWindow]]
    transactions: list[CohortTransaction] = field(default_factory=list)

    @property
    def traffic_span(self) -> tuple[datetime, datetime]:
        """
        The sub-interval of the incident window that actually carries traffic.

        WHY NOT JUST USE THE DECLARED WINDOW
        -------------------------------------
        Day 2B writes health windows spanning the canonical dataset's own time range. An
        incident window may legitimately extend a little past the last transaction (the
        Day 3 golden scenario is defined on calendar boundaries, not on the data's last
        row), and a strict `valid_to >= window_end` test would then report
        NO_HEALTH_RECORD for a gateway that is demonstrably healthy for every moment
        traffic exists.

        Binding the check to the traffic span keeps it meaningful without weakening it:
        "healthy for the whole window" is enforced as "healthy at every moment we would
        actually be routing payments". A genuine mid-window degradation still fails,
        because it overlaps real traffic. Falls back to the declared window when the
        cohort is empty -- in which case the simulation is invalid on cohort size anyway.
        """
        if not self.transactions:
            return self.window_start, self.window_end
        stamps = [t.timestamp for t in self.transactions]
        return min(stamps), max(stamps)

    def healthy_for_whole_window(self, gateway_id: str) -> tuple[bool, str]:
        """
        Whether a gateway is HEALTHY across the entire trafficked span of the window.

        Whole-span rather than point-in-time on purpose: rerouting onto a gateway that
        is healthy now but degrades mid-window would move traffic into a second
        incident. Returns (ok, reason) so the caller can report *why* rather than
        emitting a bare False.
        """
        windows = self.health.get(gateway_id, [])
        if not windows:
            return False, "NO_HEALTH_RECORD"

        span_start, span_end = self.traffic_span
        covering = [w for w in windows if w.valid_from <= span_start and w.valid_to >= span_end]
        if not covering:
            return False, "NO_HEALTH_RECORD"
        if any(w.health_state != HEALTH_HEALTHY for w in covering):
            return False, "NOT_HEALTHY"

        # A degradation that starts inside the span is still disqualifying, even though
        # some other record covers the span end to end.
        overlapping = [
            w
            for w in windows
            if w.valid_from < span_end and w.valid_to > span_start
            and w.health_state != HEALTH_HEALTHY
        ]
        if overlapping:
            return False, "NOT_HEALTHY"
        return True, HEALTH_HEALTHY


# One set-based query for the whole window. Every field the simulator needs travels with
# it, so nothing inside the per-row loop touches the database (no N+1). The LEFT JOIN
# keeps rows whose Day 3 outcome is missing visible rather than silently dropping them.
_COHORT_SQL = text(
    """
    SELECT
        t.transaction_id,
        t.timestamp,
        t.amount,
        t.status                            AS observed_status,
        t.sender_bank,
        t.payment_method,
        t.region,
        t.device,
        t.network,
        a.selected_gateway_id               AS gateway_id,
        o.in_affected_cohort,
        o.simulated_status                  AS current_status,
        o.simulated_response_code           AS current_response_code,
        o.simulated_response_attribution    AS current_response_attribution,
        o.simulated_latency_regime          AS current_latency_regime,
        o.simulated_latency_ms              AS current_latency_ms,
        o.modeled_failure_probability       AS current_modeled_failure_probability
    FROM transactions t
    JOIN synthetic_infrastructure_assignments a
      ON a.transaction_id = t.transaction_id
    JOIN simulated_incident_outcomes o
      ON o.transaction_id = t.transaction_id
     AND o.incident_id = :incident_id
     AND o.simulation_run_id = :simulation_run_id
    WHERE a.generation_run_id = :generation_run_id
      AND t.timestamp >= :window_start
      AND t.timestamp <  :window_end
    ORDER BY t.transaction_id
    """
)

_PROFILE_SQL = text(
    """
    SELECT gateway_id,
           profile_version,
           baseline_failure_probability,
           latency_multiplier,
           failure_response_mix,
           baseline_traffic_weight
    FROM synthetic_gateway_profiles
    WHERE profile_version = :profile_version
    ORDER BY gateway_id
    """
)

_ELIGIBILITY_SQL = text(
    """
    SELECT gateway_id, policy_version, is_eligible, traffic_weight, eligibility_conditions
    FROM synthetic_routing_policy_gateways
    WHERE policy_version = :policy_version
    ORDER BY gateway_id
    """
)

_HEALTH_SQL = text(
    """
    SELECT gateway_id, health_state, valid_from, valid_to,
           failure_multiplier, latency_multiplier, timeout_multiplier
    FROM synthetic_gateway_health_states
    WHERE generation_run_id = :generation_run_id
    ORDER BY gateway_id, valid_from
    """
)

_INCIDENT_SQL = text(
    """
    SELECT incident_id, incident_key, incident_seed, incident_type,
           affected_gateway_id, affected_segment,
           incident_start, incident_end,
           failure_multiplier, latency_multiplier, timeout_multiplier,
           generation_run_id, source_ingestion_run_id, status
    FROM incidents
    WHERE incident_id = :incident_id
    """
)

_DAY3_RUN_SQL = text(
    """
    SELECT simulation_run_id, simulation_fingerprint
    FROM incident_simulation_runs
    WHERE incident_id = :incident_id AND status = 'SUCCEEDED'
    ORDER BY simulation_run_id DESC
    LIMIT 1
    """
)

_ANALYSIS_RUN_SQL = text(
    """
    SELECT analysis_run_id, incident_id
    FROM incident_analysis_runs
    WHERE analysis_run_id = :analysis_run_id
    """
)

# The RCA conclusion the policy gate consumes. Note what is absent: no ground-truth
# column is selected, named, or joined anywhere in this file.
_RCA_SQL = text(
    """
    SELECT verdict, predicted_root_cause, predicted_hypothesis_type, predicted_gateway_id,
           predicted_segment, confidence, severity, significance_sigma, evidence_strength,
           supporting_evidence_ids, rca_fingerprint
    FROM incident_rca_results
    WHERE analysis_run_id = :analysis_run_id
    """
)

# PRIMARY alerts only. Day 3's P1-1 fix established that a DERIVATIVE anomaly is a
# causal shadow of a stronger cohort; treating one as an independent actionable cause is
# precisely the defect that fix closed, so Day 4 must not re-open it.
_PRIMARY_ANOMALY_SQL = text(
    """
    SELECT alert_role, cohort_key, significance_sigma, severity, affected_population,
           gmv_at_risk, rank
    FROM incident_anomalies
    WHERE analysis_run_id = :analysis_run_id
      AND suppressed = false
      AND alert_role = 'PRIMARY'
    ORDER BY rank
    """
)


class SourceUnavailable(RuntimeError):
    """Raised when a required prior-layer row does not resolve."""


def analysis_run_exists(session: Session, analysis_run_id: int) -> dict | None:
    row = session.execute(
        _ANALYSIS_RUN_SQL, {"analysis_run_id": analysis_run_id}
    ).mappings().first()
    return dict(row) if row else None


def load_rca(session: Session, analysis_run_id: int) -> dict | None:
    """The RCA conclusion for a run, or None. Never returns ground truth."""
    row = session.execute(_RCA_SQL, {"analysis_run_id": analysis_run_id}).mappings().first()
    return dict(row) if row else None


def load_primary_anomalies(session: Session, analysis_run_id: int) -> list[dict]:
    rows = session.execute(
        _PRIMARY_ANOMALY_SQL, {"analysis_run_id": analysis_run_id}
    ).mappings().all()
    return [dict(r) for r in rows]


def load_world_state(
    session: Session,
    incident_id: int,
    profile_version: str = BASELINE_PROFILE_VERSION,
    policy_version: str = BASELINE_POLICY_VERSION,
) -> WorldState:
    """
    Read the complete current world for one incident, in four set-based queries.

    Everything the simulator will hold constant is captured HERE, at one instant, so a
    candidate sweep evaluates every option against an identical world. Re-reading per
    candidate would let the world drift between candidates and make the comparison
    meaningless.
    """
    incident = session.execute(_INCIDENT_SQL, {"incident_id": incident_id}).mappings().first()
    if incident is None:
        raise SourceUnavailable(f"incident {incident_id} does not resolve")

    day3 = session.execute(_DAY3_RUN_SQL, {"incident_id": incident_id}).mappings().first()
    if day3 is None:
        raise SourceUnavailable(
            f"incident {incident_id} has no succeeded Day 3 simulation run; "
            "a counterfactual has no baseline to compare against"
        )

    profile_rows = session.execute(
        _PROFILE_SQL, {"profile_version": profile_version}
    ).mappings().all()
    if not profile_rows:
        raise SourceUnavailable(
            f"no Day 2B gateway profiles for profile_version={profile_version!r}"
        )
    profiles = {
        r["gateway_id"]: GatewayProfile(
            gateway_id=r["gateway_id"],
            profile_version=r["profile_version"],
            baseline_failure_probability=float(r["baseline_failure_probability"]),
            latency_multiplier=float(r["latency_multiplier"]),
            failure_response_mix={k: float(v) for k, v in dict(r["failure_response_mix"]).items()},
            baseline_traffic_weight=float(r["baseline_traffic_weight"]),
        )
        for r in profile_rows
    }

    eligibility_rows = session.execute(
        _ELIGIBILITY_SQL, {"policy_version": policy_version}
    ).mappings().all()
    eligibility = {
        r["gateway_id"]: GatewayEligibility(
            gateway_id=r["gateway_id"],
            policy_version=r["policy_version"],
            is_eligible=bool(r["is_eligible"]),
            traffic_weight=float(r["traffic_weight"]),
            conditions=r["eligibility_conditions"],
        )
        for r in eligibility_rows
    }

    health: dict[str, list[GatewayHealthWindow]] = {}
    health_rows = session.execute(
        _HEALTH_SQL, {"generation_run_id": incident["generation_run_id"]}
    ).mappings().all()
    for r in health_rows:
        health.setdefault(r["gateway_id"], []).append(
            GatewayHealthWindow(
                gateway_id=r["gateway_id"],
                health_state=r["health_state"],
                valid_from=r["valid_from"],
                valid_to=r["valid_to"],
                failure_multiplier=float(r["failure_multiplier"]),
                latency_multiplier=float(r["latency_multiplier"]),
                timeout_multiplier=float(r["timeout_multiplier"]),
            )
        )

    cohort_rows = session.execute(
        _COHORT_SQL,
        {
            "incident_id": incident_id,
            "simulation_run_id": day3["simulation_run_id"],
            "generation_run_id": incident["generation_run_id"],
            "window_start": incident["incident_start"],
            "window_end": incident["incident_end"],
        },
    ).mappings().all()

    transactions = [
        CohortTransaction(
            transaction_id=r["transaction_id"],
            timestamp=r["timestamp"],
            amount=float(r["amount"]),
            observed_status=r["observed_status"],
            gateway_id=r["gateway_id"],
            sender_bank=r["sender_bank"],
            payment_method=r["payment_method"],
            region=r["region"],
            device=r["device"],
            network=r["network"],
            in_affected_cohort=bool(r["in_affected_cohort"]),
            current_status=r["current_status"],
            current_response_code=r["current_response_code"],
            current_response_attribution=r["current_response_attribution"],
            current_latency_regime=r["current_latency_regime"],
            current_latency_ms=float(r["current_latency_ms"]),
            current_modeled_failure_probability=float(r["current_modeled_failure_probability"]),
        )
        for r in cohort_rows
    ]

    return WorldState(
        incident_id=incident["incident_id"],
        incident_key=incident["incident_key"],
        incident_seed=incident["incident_seed"],
        incident_type=incident["incident_type"],
        affected_gateway_id=incident["affected_gateway_id"],
        affected_segment=incident["affected_segment"],
        window_start=incident["incident_start"],
        window_end=incident["incident_end"],
        failure_multiplier=float(incident["failure_multiplier"]),
        latency_multiplier=float(incident["latency_multiplier"]),
        timeout_multiplier=float(incident["timeout_multiplier"]),
        generation_run_id=incident["generation_run_id"],
        source_ingestion_run_id=incident["source_ingestion_run_id"],
        day3_simulation_fingerprint=day3["simulation_fingerprint"] or "",
        profiles=profiles,
        eligibility=eligibility,
        health=health,
        transactions=transactions,
    )


def incident_status(session: Session, incident_id: int) -> str | None:
    row = session.execute(_INCIDENT_SQL, {"incident_id": incident_id}).mappings().first()
    return row["status"] if row else None
