"""
Deterministic risk model.

WHY RISK IS DECOMPOSED RATHER THAN SCORED
------------------------------------------
A single opaque risk number cannot be argued with. Six named components can: an operator
looking at a blocked recommendation can see that concentration was fine, health was
fine, and it was simulation quality that failed -- and can check that judgement against
the cohort size themselves. Each component is a pure function of persisted inputs, so
the same world always yields the same risk. No LLM contributes a risk value in Day 4A
(no LLM exists), and no caller may pass one in.

The aggregate `score` exists for ranking and reporting only. **The policy gate never
consults it.** Gates bind on the individual measurable constraints -- concentration,
target health, simulation validity, and the Day 3 evidence quartet -- so a low aggregate
can never wash out a specific unacceptable component. That separation is deliberate: a
blended score is precisely the failure mode Day 3's P1-2 fix identified and closed.

UNMEASURABLE MEANS UNAVAILABLE, NEVER ZERO
------------------------------------------
Capacity risk cannot be computed: no capacity telemetry exists anywhere in Day 2B. It is
reported `UNAVAILABLE` and EXCLUDED from the aggregate rather than silently treated as
0.0, because a zero would read as "capacity checked, no risk found" -- a claim about a
check that never happened.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import (
    ACTION_NO_ACTION,
    CAPACITY_UNAVAILABLE,
    ELIGIBILITY_UNCONDITIONAL,
    MIN_COHORT_SIZE,
)
from .source import WorldState

# Concentration share at which concentration risk saturates at 1.0. Aligned with the
# policy gate's 40% ceiling so a candidate approaching the bound reads as maximal risk
# before the gate refuses it, rather than jumping from "low risk" to "blocked".
CONCENTRATION_SATURATION = 0.40

# Latency increase (ms, p95) at which latency risk saturates. Anchored on Day 2B's
# measured regime separation: NORMAL medians sit near 500ms and the ELEVATED band runs
# to roughly 900ms, so a ~400ms p95 increase means the population has effectively moved
# a whole regime and is the natural saturation point.
LATENCY_SATURATION_MS = 400.0

# Cohort size at which simulation-quality risk reaches zero. Four times the minimum
# viable cohort: at MIN_COHORT_SIZE a projection is admissible but thin, and it should
# stop being flagged only once the population is comfortably beyond the floor.
COHORT_CONFIDENCE_SIZE = MIN_COHORT_SIZE * 4

# Fixed routing-uncertainty floor. `eligibility_conditions` is NULL for every gateway,
# so routing eligibility carries no discriminating information at all. This is an
# HONEST constant standing in for "this dimension cannot currently be assessed" -- it is
# not a measurement, and it is labelled as such in the persisted components.
ROUTING_UNCERTAINTY_UNCONDITIONAL = 0.25

# Weights over the five MEASURABLE components. Equal but for concentration and target
# health, which are weighted double because both describe harm the action itself would
# cause, rather than uncertainty about whether it would help.
_WEIGHTS = {
    "concentration_risk": 2.0,
    "target_health_risk": 2.0,
    "latency_risk": 1.0,
    "simulation_quality_risk": 1.0,
    "evidence_uncertainty_risk": 1.0,
    "routing_uncertainty_risk": 1.0,
}


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


@dataclass(frozen=True)
class RiskAssessment:
    """Six named components plus one aggregate. Capacity is absent, on purpose."""

    concentration_risk: float
    target_health_risk: float
    latency_risk: float
    simulation_quality_risk: float
    evidence_uncertainty_risk: float
    routing_uncertainty_risk: float
    score: float
    explanation: str

    def as_dict(self) -> dict:
        return {
            "concentration_risk": round(self.concentration_risk, 6),
            "target_health_risk": round(self.target_health_risk, 6),
            "latency_risk": round(self.latency_risk, 6),
            "simulation_quality_risk": round(self.simulation_quality_risk, 6),
            "evidence_uncertainty_risk": round(self.evidence_uncertainty_risk, 6),
            "routing_uncertainty_risk": round(self.routing_uncertainty_risk, 6),
            # Explicitly present and explicitly unmeasurable — never omitted, so a
            # reader cannot mistake its absence for "no capacity risk".
            "capacity_risk": CAPACITY_UNAVAILABLE,
            "aggregate_score": round(self.score, 6),
            "aggregate_is_advisory_only": True,
            "explanation": self.explanation,
            "basis": "DETERMINISTIC — computed from persisted inputs; no model authored any value",
        }


def compute_risk(
    world: WorldState,
    candidate,
    outcomes: list,
    impact,
    rca_confidence: float | None = None,
) -> RiskAssessment:
    """
    Deterministically assess one candidate.

    `rca_confidence` is optional because the simulator runs before a recommendation
    exists; when absent, evidence uncertainty is reported at its neutral midpoint and
    the recommendation layer's own gates carry the Day 3 quartet independently.
    """
    # --- concentration: how much of ALL window traffic ends up on the target ---------
    concentration_risk = _clamp(impact.concentration_after / CONCENTRATION_SATURATION)

    # --- target health: binary, because a degraded target is not a matter of degree ---
    if candidate.action_type == ACTION_NO_ACTION:
        target_health_risk = 0.0
        health_note = "NO_ACTION moves no traffic; no target health is at stake"
    else:
        healthy, reason = world.healthy_for_whole_window(candidate.target_gateway_id)
        target_health_risk = 0.0 if healthy else 1.0
        health_note = f"target {candidate.target_gateway_id} health across window: {reason}"

    # --- latency: only an INCREASE is a risk; an improvement is not negative risk -----
    latency_risk = _clamp(max(impact.latency_delta_ms, 0.0) / LATENCY_SATURATION_MS)

    # --- simulation quality: thin cohorts make thin projections ----------------------
    cohort_size = impact.affected_population
    simulation_quality_risk = _clamp(1.0 - (cohort_size / COHORT_CONFIDENCE_SIZE))

    # --- evidence uncertainty: taken from the RCA, never invented --------------------
    if rca_confidence is None:
        evidence_uncertainty_risk = 0.5
        evidence_note = "RCA confidence not supplied at simulation time; neutral midpoint used"
    else:
        evidence_uncertainty_risk = _clamp(1.0 - rca_confidence)
        evidence_note = f"from RCA confidence {rca_confidence:.4f}"

    # --- routing uncertainty: honest constant, not a measurement ---------------------
    routing_uncertainty_risk = ROUTING_UNCERTAINTY_UNCONDITIONAL

    components = {
        "concentration_risk": concentration_risk,
        "target_health_risk": target_health_risk,
        "latency_risk": latency_risk,
        "simulation_quality_risk": simulation_quality_risk,
        "evidence_uncertainty_risk": evidence_uncertainty_risk,
        "routing_uncertainty_risk": routing_uncertainty_risk,
    }
    weight_total = sum(_WEIGHTS.values())
    score = sum(components[k] * _WEIGHTS[k] for k in components) / weight_total

    explanation = (
        f"concentration {concentration_risk:.3f} (target share {impact.concentration_after:.4f}); "
        f"target health {target_health_risk:.3f} ({health_note}); "
        f"latency {latency_risk:.3f} (p95 delta {impact.latency_delta_ms:+.1f}ms); "
        f"simulation quality {simulation_quality_risk:.3f} (cohort n={cohort_size}); "
        f"evidence uncertainty {evidence_uncertainty_risk:.3f} ({evidence_note}); "
        f"routing uncertainty {routing_uncertainty_risk:.3f} ({ELIGIBILITY_UNCONDITIONAL}); "
        f"capacity {CAPACITY_UNAVAILABLE} (excluded from the aggregate, not assumed zero)"
    )

    return RiskAssessment(
        concentration_risk=concentration_risk,
        target_health_risk=target_health_risk,
        latency_risk=latency_risk,
        simulation_quality_risk=simulation_quality_risk,
        evidence_uncertainty_risk=evidence_uncertainty_risk,
        routing_uncertainty_risk=routing_uncertainty_risk,
        score=score,
        explanation=explanation,
    )
