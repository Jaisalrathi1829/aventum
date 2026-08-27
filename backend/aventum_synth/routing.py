"""
Synthetic routing: gateway eligibility and deterministic selection.

STATUS-CONDITIONED SELECTION -- the central modelling decision
--------------------------------------------------------------
Aventum needs gateways whose baseline failure rates genuinely differ, otherwise a later
incident has no backdrop to stand out against and per-gateway RCA is meaningless.

But `transactions.status` is observed and immutable. If gateways were assigned
independently of status, every gateway would show the same ~4.95% failure rate (the
dataset average) and the calibrated differentiation would exist only on paper.

So selection samples from the posterior P(gateway | observed status) instead of the
prior P(gateway):

    P(g | FAILED)  proportional to  w_g * p_g
    P(g | SUCCESS) proportional to  w_g * (1 - p_g)

where w_g is the policy traffic weight and p_g the gateway's calibrated failure
probability. This is Bayes applied to the forward model the profiles describe, and it
is equivalent to forward-generating outcomes and keeping only the draws that match the
observed status.

Consequences, all intended:
  - Observed marginals are preserved EXACTLY. Every observed failure is assigned to
    some gateway; none are created or destroyed.
  - Per-gateway failure rate converges to the calibrated p_g.
  - Overall traffic share converges to w_g, because the calibration normalises
    sum(w_g * p_g) to the observed failure rate (see calibration.py).

Honest framing (docs/DAY2B_TRUTH_MODEL.md): this ATTRIBUTES observed outcomes to
synthetic gateways in calibrated proportions. It does not claim a gateway caused any
particular failure. No observed field is altered.
"""

from __future__ import annotations

from dataclasses import dataclass

from .rng import LANE_GATEWAY, lane_uniform, weighted_choice

# Recorded on every assignment so the selection mechanism is never ambiguous downstream.
SELECTION_METHOD = "synthetic_deterministic_hash_weighted_status_conditioned"

ROUTING_POLICY_VERSION = "baseline-v1"
ROUTING_POLICY_DISPLAY_NAME = "Aventum synthetic baseline routing policy v1"
ROUTING_POLICY_DESCRIPTION = (
    "Synthetic weighted baseline assignment across the five Aventum model gateways. "
    "All active gateways are eligible for all traffic; selection is a deterministic "
    "hash draw from the status-conditioned posterior of the calibrated gateway weights "
    "and failure profiles. This is a modelling construct for generating a credible "
    "normal-operation baseline -- it is NOT adaptive routing and does NOT represent any "
    "real payment processor's routing algorithm."
)


@dataclass(frozen=True)
class GatewayCandidate:
    gateway_id: str
    traffic_weight: float
    failure_probability: float
    is_eligible: bool
    eligibility_reason: str


def build_candidates(
    policy_gateways: list[dict],
    failure_probabilities: dict[str, float],
) -> list[GatewayCandidate]:
    """
    Build the eligible-gateway set for the baseline policy.

    Eligibility is data-driven (`synthetic_routing_policy_gateways.is_eligible`), so a
    later policy version can scope gateways without changing this code. The reason
    string is persisted per assignment so "why was this gateway eligible?" is
    answerable from the database alone.
    """
    candidates = [
        GatewayCandidate(
            gateway_id=row["gateway_id"],
            traffic_weight=float(row["traffic_weight"]),
            failure_probability=failure_probabilities[row["gateway_id"]],
            is_eligible=bool(row["is_eligible"]),
            eligibility_reason=(
                "active gateway, unconditional eligibility under baseline policy"
                if row.get("eligibility_conditions") is None
                else f"matched conditions {row['eligibility_conditions']}"
            ),
        )
        for row in policy_gateways
    ]
    # Stable order is part of the determinism contract.
    return sorted(candidates, key=lambda c: c.gateway_id)


def select_gateway(
    digest: bytes,
    observed_status: str,
    candidates: list[GatewayCandidate],
) -> str:
    """
    Deterministically select a gateway from the status-conditioned posterior.

    `digest` is the per-transaction SHA-256; only the gateway lane is consumed, so this
    draw is independent of the latency and response draws.
    """
    eligible = [c for c in candidates if c.is_eligible]
    if not eligible:
        raise ValueError("no eligible gateways for this policy version")

    if observed_status == "FAILED":
        weights = [(c.gateway_id, c.traffic_weight * c.failure_probability) for c in eligible]
    else:
        weights = [
            (c.gateway_id, c.traffic_weight * (1.0 - c.failure_probability)) for c in eligible
        ]

    uniform = lane_uniform(digest, LANE_GATEWAY)
    return weighted_choice(uniform, weights)


def eligible_gateway_record(candidates: list[GatewayCandidate]) -> list[dict]:
    """
    The FULL reasoned eligibility snapshot, with weights and per-gateway reasons.

    Persisted ONCE per generation run (on `synthetic_generation_runs.model_parameters`),
    not per assignment: under the baseline policy every transaction sees the identical
    eligible set, so writing this on all 250k rows would be ~125 MB of byte-identical
    duplication. The per-row column keeps the compact ID list below, which is what
    actually varies once Day 2C introduces conditional eligibility.
    """
    return [
        {
            "gateway_id": c.gateway_id,
            "traffic_weight": round(c.traffic_weight, 6),
            "eligible": c.is_eligible,
            "reason": c.eligibility_reason,
        }
        for c in candidates
    ]


def eligible_gateway_ids(candidates: list[GatewayCandidate]) -> list[str]:
    """
    Compact per-assignment record: which gateways were eligible for THIS transaction.

    Joined with `routing_policy_version` (which resolves to the full reasoned snapshot),
    this answers both "why was this gateway eligible?" and "which policy version
    selected it?" without duplicating the reasons on every row.
    """
    return [c.gateway_id for c in candidates if c.is_eligible]
