"""
Deterministic business-impact calculation.

THE VOCABULARY IS PART OF THE CONTRACT
--------------------------------------
Nothing here is "recovered GMV". Recovery is a claim about the real world, and Day 4
never touches the real world: amounts are OBSERVED (`transactions.amount`) but *which*
transactions succeed is MODELLED. The permitted terms are `projected GMV retained`,
`estimated GMV retained`, `GMV at risk`, and `simulated benefit` -- and the field names
below use them, so the honesty boundary survives being copied into a UI or a report by
someone who never read this docstring.

THE PRIMARY OBJECTIVE IS AN EXPECTATION, NOT A COUNT
-----------------------------------------------------
    expected_gmv_retained = Σ amount_t × (P_success(t|target) − P_success(t|current))
                            t ∈ rerouted

It is deliberately NOT "sum the amounts of transactions whose modelled status flipped".
A flip count is one realisation of a random draw; the expectation is the quantity that
draw is sampling, and it is stable under reseeding. Optimising on the noisy realisation
would let a lucky seed outrank a genuinely better policy.

Every term is read from Day 2B profiles, Day 2B health states, and observed amounts. No
term is estimated, and none is supplied by a caller.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import ACTION_NO_ACTION
from .source import WorldState


@dataclass(frozen=True)
class BusinessImpact:
    """Deterministic projections for one candidate. Every field is derived, none passed in."""

    affected_population: int
    rerouted_population: int

    baseline_success_rate: float
    projected_success_rate: float
    expected_success_delta: float
    baseline_failure_count: int
    projected_failure_count: int

    projected_gmv_total: float
    # The primary optimization objective. Probability-weighted, not a flip count.
    expected_gmv_retained: float
    projected_gmv_at_risk: float
    baseline_gmv_at_risk: float

    baseline_latency_p50: float
    baseline_latency_p95: float
    projected_latency_p50: float
    projected_latency_p95: float
    latency_delta_ms: float

    current_distribution: dict
    projected_distribution: dict
    concentration_after: float
    concentration_before: float
    blast_radius: float

    def as_dict(self) -> dict:
        return {
            "affected_population": self.affected_population,
            "rerouted_population": self.rerouted_population,
            "baseline_success_rate": self.baseline_success_rate,
            "projected_success_rate": self.projected_success_rate,
            "expected_success_delta": self.expected_success_delta,
            "baseline_failure_count": self.baseline_failure_count,
            "projected_failure_count": self.projected_failure_count,
            "projected_gmv_total": self.projected_gmv_total,
            "expected_gmv_retained": self.expected_gmv_retained,
            "projected_gmv_at_risk": self.projected_gmv_at_risk,
            "baseline_gmv_at_risk": self.baseline_gmv_at_risk,
            "projected_latency_p50": self.projected_latency_p50,
            "projected_latency_p95": self.projected_latency_p95,
            "latency_delta_ms": self.latency_delta_ms,
            "concentration_after": self.concentration_after,
            "blast_radius": self.blast_radius,
            "gmv_basis": "OBSERVED_TRANSACTION_AMOUNTS + MODELLED_OUTCOMES",
        }


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """
    Nearest-rank percentile on a pre-sorted list.

    Nearest-rank rather than interpolated so the value is always one actually observed
    in the population -- a projected p95 latency should be a latency the model produced,
    not an average of two that straddle the rank.
    """
    if not sorted_values:
        return 0.0
    index = int(fraction * (len(sorted_values) - 1))
    return sorted_values[index]


def compute_business_impact(world: WorldState, candidate, outcomes: list) -> BusinessImpact:
    """
    Derive every business figure for one candidate from its projected outcomes.

    Two populations are in play and must not be confused:
      * the AFFECTED COHORT (`outcomes`) -- what the incident touched and a reroute may
        move; success rates and GMV are measured here.
      * the FULL WINDOW (`world.transactions`) -- every transaction in the window;
        concentration is measured here, because a gateway's traffic share is only
        meaningful against all traffic, not against one incident's cohort.
    """
    total = len(outcomes)
    baseline_successes = sum(1 for o in outcomes if o.current_status == "SUCCESS")
    projected_successes = sum(1 for o in outcomes if o.projected_status == "SUCCESS")

    baseline_success_rate = (baseline_successes / total) if total else 0.0
    projected_success_rate = (projected_successes / total) if total else 0.0

    # ---- primary objective: probability-weighted expected GMV retained --------------
    expected_gmv_retained = sum(
        o.amount * (o.p_success_projected - o.p_success_current) for o in outcomes if o.rerouted
    )

    projected_gmv_total = sum(o.amount for o in outcomes)
    projected_gmv_at_risk = sum(o.amount for o in outcomes if o.projected_status == "FAILED")
    baseline_gmv_at_risk = sum(o.amount for o in outcomes if o.current_status == "FAILED")

    baseline_latencies = sorted(o.current_latency_ms for o in outcomes)
    projected_latencies = sorted(o.projected_latency_ms for o in outcomes)
    baseline_p50 = _percentile(baseline_latencies, 0.50)
    baseline_p95 = _percentile(baseline_latencies, 0.95)
    projected_p50 = _percentile(projected_latencies, 0.50)
    projected_p95 = _percentile(projected_latencies, 0.95)

    # ---- traffic redistribution over the FULL window --------------------------------
    window_total = len(world.transactions)
    current_distribution: dict[str, int] = {}
    for txn in world.transactions:
        current_distribution[txn.gateway_id] = current_distribution.get(txn.gateway_id, 0) + 1

    projected_distribution = dict(current_distribution)
    moved = sum(1 for o in outcomes if o.rerouted)
    if moved and candidate.action_type != ACTION_NO_ACTION:
        source = candidate.source_gateway_id
        target = candidate.target_gateway_id
        projected_distribution[source] = projected_distribution.get(source, 0) - moved
        projected_distribution[target] = projected_distribution.get(target, 0) + moved

    if candidate.action_type == ACTION_NO_ACTION or not window_total:
        # NO_ACTION moves nothing, so post-action concentration is simply the largest
        # share already present. Reporting 0 here would make NO_ACTION look artificially
        # safe against a concentration bound it does not actually change.
        concentration_after = (
            max(current_distribution.values()) / window_total if window_total else 0.0
        )
        concentration_before = concentration_after
    else:
        concentration_before = current_distribution.get(candidate.target_gateway_id, 0) / window_total
        concentration_after = (
            projected_distribution.get(candidate.target_gateway_id, 0) / window_total
        )

    # Share of all in-window traffic the incident touches -- how wide the blast is.
    blast_radius = (total / window_total) if window_total else 0.0

    return BusinessImpact(
        affected_population=total,
        rerouted_population=moved,
        baseline_success_rate=baseline_success_rate,
        projected_success_rate=projected_success_rate,
        expected_success_delta=projected_success_rate - baseline_success_rate,
        baseline_failure_count=total - baseline_successes,
        projected_failure_count=total - projected_successes,
        projected_gmv_total=projected_gmv_total,
        expected_gmv_retained=expected_gmv_retained,
        projected_gmv_at_risk=projected_gmv_at_risk,
        baseline_gmv_at_risk=baseline_gmv_at_risk,
        baseline_latency_p50=baseline_p50,
        baseline_latency_p95=baseline_p95,
        projected_latency_p50=projected_p50,
        projected_latency_p95=projected_p95,
        latency_delta_ms=projected_p95 - baseline_p95,
        current_distribution=current_distribution,
        projected_distribution=projected_distribution,
        concentration_after=concentration_after,
        concentration_before=concentration_before,
        blast_radius=blast_radius,
    )
