"""
The generative outcome model: gateway state -> latency, response, attribution.

MODEL DIRECTION AND THE OBSERVED-STATUS CONSTRAINT
--------------------------------------------------
The forward model Aventum wants is:

    gateway + context + health  ->  P(failure), latency distribution, response mix

But `transactions.status` is OBSERVED FACT from the canonical dataset and is read-only
(docs/DAY2B_INTERFACE_CONTRACT.md §6). Day 2B therefore does NOT generate status. It
generates the infrastructure signals *conditioned on* the observed status, so the
synthetic layer can never contradict observed data.

The forward probability is still computed and persisted as
`modeled_failure_probability`, because Day 2C's counterfactual simulator needs it: it
is the model's belief about a (gateway, context, health) combination, independent of
what actually happened to any one transaction.

How gateway differentiation survives this constraint is handled in routing.py, not
here -- see the status-conditioned selection note there.

COHERENCE
---------
Fields are never drawn independently. The chain is:

    observed status -> response family -> latency regime -> latency value

so a TIMEOUT response always carries a TIMEOUT-regime latency, and an APPROVED response
always carries a NORMAL-regime latency. The database enforces the same invariants
(see models.py) so a bug here cannot persist an incoherent row.
"""

from __future__ import annotations

from dataclasses import dataclass

from .calibration import (
    LATENCY_REGIME_PARAMS,
    RESPONSE_APPROVED,
    RESPONSE_FAMILY_ATTRIBUTION,
    RESPONSE_TIMEOUT,
)
from .rng import LANE_LATENCY, LANE_RESPONSE, lane_uniform, lognormal_from_uniform, weighted_choice

# Fraction of SUCCESSFUL transactions that still take noticeably longer than usual.
# Real systems have a slow tail that does not fail; without it, latency would be a
# perfect predictor of outcome, which would make later RCA trivially easy and unrealistic.
SUCCESS_ELEVATED_LATENCY_RATE = 0.04


@dataclass(frozen=True)
class GatewayRuntimeProfile:
    """Effective parameters for one gateway after health multipliers are applied."""

    gateway_id: str
    profile_version: str
    baseline_failure_probability: float
    latency_multiplier: float
    failure_response_mix: dict[str, float]
    health_state: str
    failure_multiplier: float
    health_latency_multiplier: float
    timeout_multiplier: float

    @property
    def effective_failure_probability(self) -> float:
        """
        Forward-model failure probability under the current health state.

        Day 2B baseline always has multiplier 1.0 (all gateways HEALTHY). Day 2C raises
        it to express a degradation, and every downstream signal follows automatically
        -- which is the point of routing all health influence through this one place.
        """
        value = self.baseline_failure_probability * self.failure_multiplier
        return min(max(value, 0.0), 0.999999)

    @property
    def effective_latency_multiplier(self) -> float:
        return self.latency_multiplier * self.health_latency_multiplier

    def effective_response_mix(self) -> dict[str, float]:
        """
        Failure-response mix with the health-driven timeout tilt applied.

        A degraded gateway should not merely fail more -- it should fail DIFFERENTLY,
        shifting toward infrastructure-side families. That is what makes the response
        distribution diagnostic evidence for a later RCA component rather than noise.
        """
        if self.timeout_multiplier == 1.0:
            return dict(self.failure_response_mix)

        mix = dict(self.failure_response_mix)
        original_timeout = mix.get(RESPONSE_TIMEOUT, 0.0)
        boosted = min(original_timeout * self.timeout_multiplier, 0.95)
        delta = boosted - original_timeout
        mix[RESPONSE_TIMEOUT] = boosted

        # Take the additional timeout share proportionally from the other families so
        # the mix still sums to 1.
        others = {k: v for k, v in mix.items() if k != RESPONSE_TIMEOUT}
        others_total = sum(others.values())
        if others_total > 0:
            for key in others:
                mix[key] = max(others[key] - delta * (others[key] / others_total), 0.0)
        return mix


def choose_response_code(
    digest: bytes,
    observed_status: str,
    profile: GatewayRuntimeProfile,
) -> tuple[str, str]:
    """
    Pick the response code, conditioned on the OBSERVED outcome.

    A successful transaction is always APPROVED -- there is no coherent synthetic story
    in which the gateway declined a payment the canonical data records as succeeding.
    A failed transaction draws a decline family from the gateway's mix.

    Returns (response_code, attribution).
    """
    if observed_status == "SUCCESS":
        return RESPONSE_APPROVED, "approved"

    mix = profile.effective_response_mix()
    # Sorted for a stable uniform -> outcome mapping (determinism contract).
    options = sorted(mix.items())
    uniform = lane_uniform(digest, LANE_RESPONSE)
    response = weighted_choice(uniform, options)
    return response, RESPONSE_FAMILY_ATTRIBUTION[response]


def choose_latency_regime(
    digest: bytes,
    observed_status: str,
    response_code: str,
) -> str:
    """
    Derive the latency regime from the outcome and response family.

    Deterministic given the response code, except for the slow-but-successful tail,
    which is drawn from the latency lane.
    """
    if response_code == RESPONSE_TIMEOUT:
        return "TIMEOUT"

    if observed_status == "FAILED":
        # Non-timeout declines still cost more time than a clean approval.
        return "ELEVATED"

    # SUCCESS: mostly normal, with a small genuinely-slow tail.
    uniform = lane_uniform(digest, LANE_LATENCY)
    return "ELEVATED" if uniform < SUCCESS_ELEVATED_LATENCY_RATE else "NORMAL"


def draw_latency_ms(
    digest: bytes,
    latency_regime: str,
    profile: GatewayRuntimeProfile,
) -> float:
    """
    Draw a latency value inside the regime's documented band.

    Right-skewed (lognormal) and clamped to the regime's floor/cap, so a NORMAL-regime
    draw can never wander into timeout territory regardless of the uniform.
    """
    params = LATENCY_REGIME_PARAMS[latency_regime]
    uniform = lane_uniform(digest, LANE_LATENCY)

    # The SUCCESS/ELEVATED branch already consumed this lane to decide the regime;
    # re-using the same uniform for the magnitude would correlate the two. Fold it so
    # the magnitude draw is decorrelated but still fully deterministic.
    folded = (uniform * 997.0) % 1.0

    median = params["median_ms"] * profile.effective_latency_multiplier
    value = lognormal_from_uniform(
        folded,
        median=median,
        sigma=params["sigma"],
        floor=params["floor_ms"],
        cap=params["cap_ms"],
    )
    return round(value, 2)


def generate_signals(
    digest: bytes,
    observed_status: str,
    profile: GatewayRuntimeProfile,
) -> dict[str, object]:
    """
    Produce the full coherent signal set for one transaction.

    Order matters: response family first, then regime, then magnitude -- each step
    constrains the next, which is what keeps the record internally consistent.
    """
    response_code, attribution = choose_response_code(digest, observed_status, profile)
    latency_regime = choose_latency_regime(digest, observed_status, response_code)
    latency_ms = draw_latency_ms(digest, latency_regime, profile)

    return {
        "gateway_response_code": response_code,
        "response_attribution": attribution,
        "latency_regime": latency_regime,
        "gateway_latency_ms": latency_ms,
        "gateway_health_state": profile.health_state,
        "modeled_failure_probability": round(profile.effective_failure_probability, 6),
        "gateway_profile_version": profile.profile_version,
    }
