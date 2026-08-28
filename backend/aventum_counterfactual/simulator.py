"""
The counterfactual simulator.

    "What would the incident window have looked like if a bounded share of the affected
     cohort had been routed to a different gateway?"

EXACTLY ONE VARIABLE CHANGES: TRAFFIC ALLOCATION
------------------------------------------------
Everything else is held constant BY CONSTRUCTION rather than by assertion, because the
same transaction rows, amounts, window, incident, multipliers, profiles, health states,
seed lane, and model versions are reused verbatim. The result row persists both
`held_constant` and `changed_variables` so the claim is auditable rather than trusted.

THE PROBABILITY MODEL IS BORROWED, NOT REBUILT
----------------------------------------------
`build_runtime_profile`, `added_failure_probability`, `is_in_affected_cohort` and
`incident_digest_for` are imported from Day 3; `generate_signals` from Day 2B. There is
no Day 4 failure formula, no Day 4 latency formula, and no Day 4 response model. A
second implementation that agreed today would diverge the first time either side moved,
and the whole comparison to the Day 3 baseline would quietly stop meaning anything.

The strongest consequence: NO_ACTION reproduces Day 3's stored outcomes EXACTLY, because
it reroutes nothing and therefore reuses every row verbatim. Candidates are measured
against a real simulated baseline, never an assumed one.

APPROACH B IS PRESERVED
-----------------------
An observed FAILED transaction is simulated FAILED under every candidate. A reroute can
prevent a MODELLED incident-induced failure; it can never rescue a historical one.
`transactions` is read-only throughout.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

# Day 3's authoritative machinery. Imported, never reimplemented.
from aventum_incident import INCIDENT_CONFIG_VERSION, INCIDENT_MODEL_VERSION
from aventum_incident.rng import LANE_OUTCOME, incident_digest_for, lane_uniform
from aventum_incident.simulate import (
    added_failure_probability,
    build_runtime_profile,
    is_in_affected_cohort,
)

# Day 2B's coherent signal funnel.
from aventum_synth.outcome_model import GatewayRuntimeProfile, generate_signals

from . import COUNTERFACTUAL_CONFIG_VERSION, COUNTERFACTUAL_MODEL_VERSION
from .constants import (
    ACTION_NO_ACTION,
    ACTION_REROUTE,
    BASELINE_POLICY_VERSION,
    BASELINE_PROFILE_VERSION,
    CAPACITY_UNAVAILABLE,
    INVALID_COHORT_EMPTY,
    INVALID_COHORT_TOO_SMALL,
    INVALID_NO_SOURCE_GATEWAY,
    INVALID_SOURCE_EQUALS_TARGET,
    INVALID_TARGET_NOT_ELIGIBLE,
    INVALID_TARGET_NOT_HEALTHY,
    INVALID_TARGET_NO_HEALTH_RECORD,
    INVALID_TARGET_UNKNOWN,
    INVALID_TRAFFIC_EXCEEDS_MAX,
    MAX_TRAFFIC_PERCENTAGE,
    MIN_COHORT_SIZE,
    PROVENANCE_OBSERVED,
    PROVENANCE_SIMULATED,
    PROVENANCE_SYNTHETIC,
    STATUS_INVALID,
    STATUS_VALID,
)
from .fingerprint import compute_input_fingerprint, compute_simulation_fingerprint
from .impact import BusinessImpact, compute_business_impact
from .models import CounterfactualSimulation
from .risk import compute_risk
from .source import CohortTransaction, WorldState


@dataclass(frozen=True)
class Candidate:
    """One policy option to evaluate. `NO_ACTION` is a first-class member, not a null."""

    action_type: str
    target_gateway_id: str | None = None
    traffic_percentage: float = 0.0
    source_gateway_id: str | None = None

    @property
    def key(self) -> str:
        """Stable identity string, part of the simulation idempotency key."""
        if self.action_type == ACTION_NO_ACTION:
            return ACTION_NO_ACTION
        return (
            f"{ACTION_REROUTE}:{self.source_gateway_id}->{self.target_gateway_id}"
            f"@{self.traffic_percentage:.1f}"
        )


@dataclass(frozen=True)
class ProjectedOutcome:
    """One transaction's outcome under the candidate policy."""

    transaction_id: str
    amount: float
    observed_status: str
    current_gateway_id: str
    projected_gateway_id: str
    rerouted: bool
    current_status: str
    projected_status: str
    current_latency_ms: float
    projected_latency_ms: float
    projected_response_code: str
    projected_latency_regime: str
    # P(success) under the CURRENT allocation and under the CANDIDATE allocation.
    # Both come from GatewayRuntimeProfile.effective_failure_probability -- the single
    # authoritative source. These two values are what the GMV objective integrates.
    p_success_current: float
    p_success_projected: float


@dataclass
class SimulationOutput:
    """The full result of one candidate evaluation."""

    status: str
    candidate: Candidate
    invalid_reason: str | None = None
    outcomes: list[ProjectedOutcome] = field(default_factory=list)
    impact: BusinessImpact | None = None
    risk_score: float | None = None
    risk_components: dict | None = None
    input_fingerprint: str = ""
    simulation_fingerprint: str | None = None
    held_constant: dict = field(default_factory=dict)
    changed_variables: dict = field(default_factory=dict)
    assumptions: dict = field(default_factory=dict)
    limitations: dict = field(default_factory=dict)
    eligibility_result: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    seed: str = ""

    @property
    def is_valid(self) -> bool:
        return self.status == STATUS_VALID


def candidate_seed(world: WorldState, candidate: Candidate) -> str:
    """
    Deterministic seed for one candidate, derived from the incident and the candidate.

    Derived rather than supplied so the same (incident, candidate) always simulates
    identically without the caller having to remember a seed -- and so a caller cannot
    reshuffle the reroute selection by passing a different one.
    """
    return f"{world.incident_seed}#{candidate.key}"


# Domain separator for the reroute-selection hash.
#
# THIS PREFIX IS LOAD-BEARING, AND ITS ABSENCE WAS A REAL BUG.
# ------------------------------------------------------------
# Without it, this function's payload string was byte-identical to Day 3's
# `incident_assignment_key(transaction_id, incident_key, seed, model_v, config_v)` --
# same fields, same order, same separator, same version values. So both produced the
# SAME SHA-256 digest.
#
# Day 3 flips an observed SUCCESS to FAILED when `lane_uniform(digest, LANE_OUTCOME)`
# (the digest's leading 8 bytes, scaled) falls below p_add. Sorting by that same
# digest's hex ascending therefore ordered the cohort by *exactly* the quantity that
# decided failure -- so a 10% reroute selected the 26 transactions the incident had
# damaged most, and every reroute appeared to rescue a failure with perfect efficiency.
# Measured on the flagship incident: 26 of 26 rerouted rows "recovered", and the
# expected benefit was overstated by roughly 5x.
#
# The prefix makes the selection stream independent of the outcome stream. Any change to
# it reshuffles every reroute selection, so it must not be edited casually.
_SELECTION_DOMAIN = "AVENTUM_REROUTE_SELECTION_V1"


def _selection_rank(transaction_id: str, world: WorldState) -> str:
    """
    The hash-ordering key deciding WHICH transactions reroute.

    Deliberately independent of the target gateway and of the traffic percentage. Two
    consequences follow, both wanted:

      * 10% ⊂ 20% ⊂ 30% -- raising the shift ADDS transactions rather than swapping the
        set, so comparing candidates compares nested populations and the benefit curve
        is monotone in a way an operator can reason about.
      * Rerouting 20% to gateway_A and 20% to gateway_B moves the SAME transactions, so
        the two targets are compared on identical traffic rather than on two different
        random subsets that might differ in amount or difficulty.

    And, per `_SELECTION_DOMAIN` above, it must also be independent of the DRAW that
    decided each transaction's incident outcome -- otherwise the "which transactions
    move" question silently answers "the ones that failed", which is not a routing
    policy any real system could implement (it would require knowing the future).

    SHA-256 keyed on the incident, never `hash()` (salted per process), never DB row
    order, never wall-clock.
    """
    payload = (
        f"{_SELECTION_DOMAIN}|{transaction_id}|{world.incident_key}|{world.incident_seed}|"
        f"{COUNTERFACTUAL_MODEL_VERSION}|{COUNTERFACTUAL_CONFIG_VERSION}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def select_rerouted(
    cohort: list[CohortTransaction], world: WorldState, traffic_percentage: float
) -> set[str]:
    """
    Deterministically choose the rerouted subset: a hash-ordered take of the cohort.

    Uses floor(), not round(): a 10% shift must never move more than 10% of traffic, and
    rounding up would silently exceed the bound the policy gate is about to check.
    """
    if traffic_percentage <= 0 or not cohort:
        return set()
    ordered = sorted(cohort, key=lambda t: (_selection_rank(t.transaction_id, world), t.transaction_id))
    count = int(len(ordered) * traffic_percentage / 100.0)
    return {t.transaction_id for t in ordered[:count]}


def runtime_profile_for(
    world: WorldState, gateway_id: str, degraded: bool
) -> GatewayRuntimeProfile:
    """
    Build the Day 3 runtime profile for one gateway under this incident.

    Delegates to Day 3's `build_runtime_profile` with a lightweight incident shim, so
    the failure/latency/timeout multipliers reach `GatewayRuntimeProfile` through
    exactly the path Day 3 uses. This function contains no arithmetic of its own --
    which is the point.
    """

    class _IncidentShim:
        failure_multiplier = world.failure_multiplier
        latency_multiplier = world.latency_multiplier
        timeout_multiplier = world.timeout_multiplier

    return build_runtime_profile(
        world.profiles[gateway_id].as_profile_row(), _IncidentShim(), degraded
    )


def p_success(profile: GatewayRuntimeProfile) -> float:
    """
    P(success | gateway, incident state) — the single authoritative definition.

    Nothing else in Day 4 computes a success probability. It is one minus Day 2B's
    `effective_failure_probability`, the same quantity Day 3 uses to drive its outcome
    draws, so a probability quoted in a Day 4 recommendation and a probability used in a
    Day 3 simulation are the same number by construction rather than by coincidence.
    """
    return 1.0 - profile.effective_failure_probability


def _validate(
    world: WorldState, candidate: Candidate, cohort: list[CohortTransaction]
) -> str | None:
    """
    Every reason a controlled counterfactual cannot be constructed.

    Returns a machine-readable reason, or None if the comparison is sound. Returning a
    reason INSTEAD of a number is the whole point: a projection built on an unhealthy
    target or an empty cohort would be a fabricated figure wearing a real one's clothes.
    """
    if not cohort:
        return INVALID_COHORT_EMPTY
    if len(cohort) < MIN_COHORT_SIZE:
        return INVALID_COHORT_TOO_SMALL

    if candidate.action_type == ACTION_NO_ACTION:
        return None

    if candidate.traffic_percentage > MAX_TRAFFIC_PERCENTAGE:
        return INVALID_TRAFFIC_EXCEEDS_MAX
    if candidate.source_gateway_id is None:
        return INVALID_NO_SOURCE_GATEWAY
    if candidate.target_gateway_id == candidate.source_gateway_id:
        return INVALID_SOURCE_EQUALS_TARGET

    target = candidate.target_gateway_id
    if target not in world.profiles:
        return INVALID_TARGET_UNKNOWN

    eligibility = world.eligibility.get(target)
    if eligibility is None or not eligibility.is_eligible:
        return INVALID_TARGET_NOT_ELIGIBLE

    healthy, reason = world.healthy_for_whole_window(target)
    if not healthy:
        return (
            INVALID_TARGET_NO_HEALTH_RECORD
            if reason == "NO_HEALTH_RECORD"
            else INVALID_TARGET_NOT_HEALTHY
        )
    return None


def _project_outcomes(
    world: WorldState, candidate: Candidate, cohort: list[CohortTransaction], rerouted: set[str]
) -> list[ProjectedOutcome]:
    """
    Regenerate outcomes for rerouted transactions; reuse Day 3's verbatim for the rest.

    Both halves matter. Regenerating a non-rerouted row would inject variance the reroute
    did not cause, making the candidate look different from the baseline for no reason.
    Carrying a rerouted row's degraded outcome forward would understate the benefit and
    make every intervention look useless.
    """
    seed = candidate_seed(world, candidate)
    outcomes: list[ProjectedOutcome] = []

    for txn in cohort:
        current_degraded = is_in_affected_cohort(txn.as_cohort_row(), _incident_view(world))
        current_profile = runtime_profile_for(world, txn.gateway_id, current_degraded)
        p_current = p_success(current_profile)

        if txn.transaction_id not in rerouted:
            # Not rerouted: the Day 3 row IS the projection, byte for byte.
            outcomes.append(
                ProjectedOutcome(
                    transaction_id=txn.transaction_id,
                    amount=txn.amount,
                    observed_status=txn.observed_status,
                    current_gateway_id=txn.gateway_id,
                    projected_gateway_id=txn.gateway_id,
                    rerouted=False,
                    current_status=txn.current_status,
                    projected_status=txn.current_status,
                    current_latency_ms=txn.current_latency_ms,
                    projected_latency_ms=txn.current_latency_ms,
                    projected_response_code=txn.current_response_code,
                    projected_latency_regime=txn.current_latency_regime,
                    p_success_current=p_current,
                    p_success_projected=p_current,
                )
            )
            continue

        target = candidate.target_gateway_id
        # Re-ask Day 3's own cohort predicate with the gateway swapped. For a gateway
        # incident the answer flips to False (the reroute escapes the blast radius); for
        # an issuer incident it stays True, because moving gateway does not change the
        # sender bank. Rerouting therefore correctly fails to fix an issuer problem.
        target_degraded = is_in_affected_cohort(
            txn.as_cohort_row(gateway_id=target), _incident_view(world)
        )
        target_profile = runtime_profile_for(world, target, target_degraded)

        # The SAME digest Day 3 would use — same transaction, same incident, same lanes.
        digest = incident_digest_for(
            transaction_id=txn.transaction_id,
            incident_key=world.incident_key,
            simulation_seed=seed,
            incident_model_version=INCIDENT_MODEL_VERSION,
            incident_config_version=INCIDENT_CONFIG_VERSION,
        )

        if txn.observed_status == "FAILED":
            # Approach B: an observed failure is never rescued by a reroute.
            projected_status = "FAILED"
        else:
            p_add = added_failure_probability(target_profile)
            projected_status = (
                "FAILED" if lane_uniform(digest, LANE_OUTCOME) < p_add else "SUCCESS"
            )

        signals = generate_signals(digest, projected_status, target_profile)
        outcomes.append(
            ProjectedOutcome(
                transaction_id=txn.transaction_id,
                amount=txn.amount,
                observed_status=txn.observed_status,
                current_gateway_id=txn.gateway_id,
                projected_gateway_id=target,
                rerouted=True,
                current_status=txn.current_status,
                projected_status=projected_status,
                current_latency_ms=txn.current_latency_ms,
                projected_latency_ms=float(signals["gateway_latency_ms"]),
                projected_response_code=str(signals["gateway_response_code"]),
                projected_latency_regime=str(signals["latency_regime"]),
                p_success_current=p_current,
                p_success_projected=p_success(target_profile),
            )
        )
    return outcomes


class _IncidentView:
    """Minimal duck-typed incident for Day 3's `is_in_affected_cohort`."""

    __slots__ = ("affected_gateway_id", "affected_segment")

    def __init__(self, affected_gateway_id: str | None, affected_segment: dict | None) -> None:
        self.affected_gateway_id = affected_gateway_id
        self.affected_segment = affected_segment


def _incident_view(world: WorldState) -> _IncidentView:
    return _IncidentView(world.affected_gateway_id, world.affected_segment)


def affected_cohort(world: WorldState) -> list[CohortTransaction]:
    """
    The incident's affected population — the only transactions a reroute may move.

    Read from Day 3's persisted `in_affected_cohort` flag rather than recomputed, so the
    simulator operates on exactly the population Day 3 diagnosed. Recomputing could
    silently disagree with the diagnosis the recommendation is going to cite.
    """
    return [t for t in world.transactions if t.in_affected_cohort]


def run_counterfactual(
    session: Session,
    world: WorldState,
    analysis_run_id: int,
    candidate: Candidate,
    policy_version: str = BASELINE_POLICY_VERSION,
    profile_version: str = BASELINE_PROFILE_VERSION,
) -> CounterfactualSimulation:
    """
    Evaluate one candidate and persist it. Idempotent on
    (incident_id, candidate_key, input_fingerprint) — re-simulating identical inputs
    returns the existing row rather than minting a second, divergent projection.
    """
    started = time.perf_counter()
    seed = candidate_seed(world, candidate)
    input_fingerprint = compute_input_fingerprint(world, seed, policy_version)

    existing = (
        session.query(CounterfactualSimulation)
        .filter_by(
            incident_id=world.incident_id,
            candidate_key=candidate.key,
            input_fingerprint=input_fingerprint,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing

    cohort = affected_cohort(world)
    # Default the source to the incident's affected gateway when the caller did not
    # name one, so a caller cannot accidentally construct a reroute with no origin.
    resolved = candidate
    if candidate.action_type == ACTION_REROUTE and candidate.source_gateway_id is None:
        resolved = Candidate(
            action_type=candidate.action_type,
            target_gateway_id=candidate.target_gateway_id,
            traffic_percentage=candidate.traffic_percentage,
            source_gateway_id=world.affected_gateway_id,
        )

    invalid_reason = _validate(world, resolved, cohort)
    eligibility_result = {
        gid: {
            "is_eligible": e.is_eligible,
            "basis": e.basis,
            "traffic_weight": e.traffic_weight,
            "health": world.healthy_for_whole_window(gid)[1],
        }
        for gid, e in sorted(world.eligibility.items())
    }

    common = {
        "incident_id": world.incident_id,
        "analysis_run_id": analysis_run_id,
        "agent_run_id": None,  # Day 4A is deterministic; no agent exists.
        "candidate_key": resolved.key,
        "action_type": resolved.action_type,
        "source_gateway_id": (
            resolved.source_gateway_id if resolved.action_type == ACTION_REROUTE else None
        ),
        "target_gateway_id": resolved.target_gateway_id,
        "traffic_percentage": resolved.traffic_percentage,
        "simulation_seed": seed,
        "input_fingerprint": input_fingerprint,
        "model_version": COUNTERFACTUAL_MODEL_VERSION,
        "policy_version": policy_version,
        "profile_version": profile_version,
        "eligibility_result": eligibility_result,
        "affected_population": len(cohort),
        # NEVER populated. No capacity telemetry exists anywhere in Day 2B.
        "capacity_utilization": None,
    }

    if invalid_reason is not None:
        row = CounterfactualSimulation(
            **common,
            status=STATUS_INVALID,
            invalid_reason=invalid_reason,
            held_constant=_held_constant(world, cohort),
            changed_variables={},
            assumptions=_assumptions(world),
            limitations=_limitations(invalid_reason),
            elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
        )
        session.add(row)
        session.flush()
        return row

    rerouted = select_rerouted(cohort, world, resolved.traffic_percentage)
    outcomes = _project_outcomes(world, resolved, cohort, rerouted)
    impact = compute_business_impact(world, resolved, outcomes)
    risk = compute_risk(world, resolved, outcomes, impact)

    rendered = [
        f"{o.transaction_id}|{o.projected_gateway_id}|{o.projected_status}|"
        f"{o.projected_response_code}|{o.projected_latency_regime}|{o.projected_latency_ms:.2f}|"
        f"{int(o.rerouted)}"
        for o in outcomes
    ]

    row = CounterfactualSimulation(
        **common,
        status=STATUS_VALID,
        invalid_reason=None,
        rerouted_population=len(rerouted),
        current_distribution=impact.current_distribution,
        projected_distribution=impact.projected_distribution,
        baseline_success_rate=round(impact.baseline_success_rate, 6),
        projected_success_rate=round(impact.projected_success_rate, 6),
        expected_success_delta=round(impact.expected_success_delta, 6),
        projected_failure_count=impact.projected_failure_count,
        projected_gmv_total=round(impact.projected_gmv_total, 2),
        projected_gmv_retained=round(impact.expected_gmv_retained, 2),
        projected_gmv_at_risk=round(impact.projected_gmv_at_risk, 2),
        projected_latency_p50=round(impact.projected_latency_p50, 2),
        projected_latency_p95=round(impact.projected_latency_p95, 2),
        latency_delta_ms=round(impact.latency_delta_ms, 2),
        concentration_after=round(impact.concentration_after, 4),
        risk_score=round(risk.score, 6),
        risk_components=risk.as_dict(),
        held_constant=_held_constant(world, cohort),
        changed_variables=_changed_variables(resolved, len(rerouted), impact),
        assumptions=_assumptions(world),
        limitations=_limitations(None),
        simulation_fingerprint=compute_simulation_fingerprint(rendered),
        elapsed_ms=round((time.perf_counter() - started) * 1000.0, 3),
    )
    session.add(row)
    session.flush()
    return row


def _held_constant(world: WorldState, cohort: list[CohortTransaction]) -> dict:
    """The counterfactual's self-audit of what it froze. Persisted, so it is checkable."""
    return {
        "transaction_population": len(cohort),
        "transaction_ids": "FROZEN — same set under every candidate",
        "transaction_amounts": PROVENANCE_OBSERVED,
        "payment_mix": "FROZEN",
        "cohort_definition": {
            "affected_gateway_id": world.affected_gateway_id,
            "affected_segment": world.affected_segment,
        },
        "incident_id": world.incident_id,
        "incident_window": {
            "start": world.window_start.isoformat(),
            "end": world.window_end.isoformat(),
        },
        "incident_multipliers": {
            "failure": world.failure_multiplier,
            "latency": world.latency_multiplier,
            "timeout": world.timeout_multiplier,
        },
        "gateway_health": "FROZEN — read once per sweep",
        "gateway_profiles": sorted(world.profiles),
        "model_version": COUNTERFACTUAL_MODEL_VERSION,
        "config_version": COUNTERFACTUAL_CONFIG_VERSION,
        "incident_model_version": INCIDENT_MODEL_VERSION,
        "seed_semantics": "derived from (incident_seed, candidate_key)",
        "eligibility_assumptions": "baseline-v1, unconditional",
    }


def _changed_variables(candidate: Candidate, rerouted_count: int, impact: BusinessImpact) -> dict:
    """
    The ONE variable that moved. Anything else appearing here is a defect, and the test
    suite asserts this dict never grows a second independent key.
    """
    if candidate.action_type == ACTION_NO_ACTION:
        return {"traffic_allocation": "UNCHANGED — this is the baseline"}
    return {
        "traffic_allocation": {
            "source_gateway": candidate.source_gateway_id,
            "target_gateway": candidate.target_gateway_id,
            "percentage_of_affected_cohort": candidate.traffic_percentage,
            "transactions_moved": rerouted_count,
            "selection": "deterministic SHA-256 hash-ordered take",
            "concentration_after": impact.concentration_after,
        }
    }


def _assumptions(world: WorldState) -> dict:
    """Every value labelled by the epistemic layer it came from."""
    return {
        "transaction_amounts": PROVENANCE_OBSERVED,
        "transaction_status_history": PROVENANCE_OBSERVED,
        "gateway_identity_and_profiles": PROVENANCE_SYNTHETIC,
        "gateway_health_states": PROVENANCE_SYNTHETIC,
        "incident": PROVENANCE_SIMULATED,
        "projected_outcomes": PROVENANCE_SIMULATED,
        "capacity": CAPACITY_UNAVAILABLE,
        "eligibility_basis": (
            world.eligibility[next(iter(sorted(world.eligibility)))].basis
            if world.eligibility
            else CAPACITY_UNAVAILABLE
        ),
        "approach": "B — modelled failures added; observed failures never reallocated",
    }


def _limitations(invalid_reason: str | None) -> dict:
    """Stated plainly on every row, valid or not."""
    limitations = {
        "capacity": "No gateway capacity telemetry exists in this dataset. Not estimated.",
        "eligibility": (
            "eligibility_conditions is NULL for all gateways under baseline-v1, so "
            "eligibility is unconditional. No substantive eligibility check occurred."
        ),
        "gmv": (
            "Amounts are observed; which transactions succeed is modelled. This is "
            "projected GMV retained, never recovered GMV."
        ),
        "execution": "Day 4 execution is SIMULATED. No real payment infrastructure exists.",
        "post_action_outcome": (
            "The dataset is static; a post-action outcome is a modelled continuation, "
            "not a measured one."
        ),
    }
    if invalid_reason is not None:
        limitations["invalid"] = (
            f"No projection was produced ({invalid_reason}). No number on this row may "
            "be read as an estimate."
        )
    return limitations
