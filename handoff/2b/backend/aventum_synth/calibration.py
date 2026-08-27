"""
Calibration transfer from the Nigerian routing reference to Aventum's synthetic model.

CRITICAL BOUNDARY
-----------------
The Nigerian Card Payment Dataset for Predictive Routing is an INDEPENDENT, itself-
synthetic calibration reference (docs/ROUTING_DATASET_AUDIT.md §4). Per
docs/ROUTING_DATASET_DECISION.md it is classification **B -- simulation/calibration
data**: its measured distributions may inform Aventum's synthetic parameters, and
nothing else.

  - No row of it is ever imported.
  - It is never joined to a UPI transaction.
  - Its values are never presented as observed evidence about a UPI transaction.

Every constant below is a *reference measurement* or a *derived Aventum parameter*, and
the two are deliberately kept in separate namespaces so a reader can always tell which
is which. The transfer rationale for each is in docs/DAY2B_CALIBRATION_SPEC.md.
"""

from __future__ import annotations

from dataclasses import dataclass

CALIBRATION_REFERENCE_NAME = "nigerian_card_payment_routing"
CALIBRATION_REFERENCE_VERSION = "day1.5-audit-2026-08"
CALIBRATION_REFERENCE_NOTE = (
    "Synthetic calibration reference only (docs/ROUTING_DATASET_DECISION.md class B). "
    "Not production telemetry. Never joined to UPI transactions."
)


# ==========================================================================
# REFERENCE MEASUREMENTS -- what the calibration dataset actually showed.
# These are facts ABOUT THE REFERENCE DATASET, not about UPI or about Aventum.
# Source: docs/ROUTING_DATASET_AUDIT.md §11, §12.
# ==========================================================================

# Per-rail failure rate (%) measured across 2,026,891 reference rows.
REFERENCE_RAIL_FAILURE_RATE_PCT: dict[str, float] = {
    "rail_A": 1.85,
    "rail_D": 2.39,
    "rail_B": 2.85,
    "rail_E": 3.40,
    "rail_C": 3.83,
}

# Per-rail share of reference traffic (fraction).
REFERENCE_RAIL_TRAFFIC_SHARE: dict[str, float] = {
    "rail_B": 0.278,
    "rail_A": 0.264,
    "rail_D": 0.209,
    "rail_C": 0.128,
    "rail_E": 0.121,
}

REFERENCE_OVERALL_FAILURE_RATE_PCT = 2.68

# Latency by outcome family (ms). Reference showed three clean, separated regimes.
REFERENCE_LATENCY_MS = {
    "approved_mean": 500.08,
    "approved_std": 119.94,
    "approved_p50": 500.07,
    "non_timeout_failure_mean": 899.0,     # the four non-timeout decline families cluster here
    "non_timeout_failure_std": 250.0,
    "timeout_mean": 3986.16,
    "timeout_std": 799.08,
    "timeout_floor": 2000.0,
}

# Share of FAILURES falling into each reference response family.
REFERENCE_FAILURE_RESPONSE_SHARE: dict[str, float] = {
    "Insufficient Funds": 0.2454,
    "Processing Error": 0.2439,
    "Issuer Declined": 0.2423,
    "Do Not Honor": 0.2389,
    "Timeout": 0.0295,
}


# ==========================================================================
# TRANSFER PARAMETERS -- explicit, documented choices, not copied values.
# ==========================================================================

# Damping applied to the reference's inter-gateway failure-rate spread.
#
# The reference spans 1.85%-3.83% (a 2.07x ratio) in what it calls normal operation.
# Transferring that ratio undamped would make Aventum's WORST baseline gateway roughly
# twice as bad as its best before any incident exists -- which risks (a) reading as an
# incident in its own right, and (b) leaving a later injected degradation with little
# headroom to stand out against. It is also a different payment ecosystem (Nigerian card
# rails vs Indian UPI), so the MAGNITUDE of the spread is not directly transferable even
# though its STRUCTURE is informative.
#
# lambda = 0.6 keeps the ordering and relative structure but compresses the spread to
# roughly 1.5x, which is differentiated-but-unambiguously-normal. This is a BOUNDED
# TRANSFER, not a direct one.
FAILURE_SPREAD_DAMPING = 0.6

# Aventum's five synthetic gateways, and which reference rail's relative profile
# informed each. The mapping is a modelling convenience: gateway_A is NOT rail_A, it is
# an Aventum entity whose relative characteristics were informed by rail_A's.
GATEWAY_CALIBRATION_SOURCE: dict[str, str] = {
    "gateway_A": "rail_A",
    "gateway_B": "rail_B",
    "gateway_C": "rail_C",
    "gateway_D": "rail_D",
    "gateway_E": "rail_E",
}

# Baseline traffic weights. CONCEPTUAL TEMPLATE from the reference's uneven-but-
# substantial split, rounded to clean values so the configuration is legible.
GATEWAY_TRAFFIC_WEIGHT: dict[str, float] = {
    "gateway_A": 0.26,
    "gateway_B": 0.27,
    "gateway_C": 0.13,
    "gateway_D": 0.21,
    "gateway_E": 0.13,
}

# Aventum latency model (ms), lognormal per regime. SCALED TRANSFER: the three-regime
# STRUCTURE and the approximate ratios between regimes are taken from the reference;
# the absolute values are Aventum's own and are not claimed to represent real UPI
# latency. Expressed as median + sigma of the underlying normal (log space).
LATENCY_REGIME_PARAMS: dict[str, dict[str, float]] = {
    "NORMAL":   {"median_ms": 420.0, "sigma": 0.32, "floor_ms": 40.0,   "cap_ms": 1800.0},
    "ELEVATED": {"median_ms": 860.0, "sigma": 0.30, "floor_ms": 180.0,  "cap_ms": 1990.0},
    "TIMEOUT":  {"median_ms": 3400.0, "sigma": 0.24, "floor_ms": 2000.0, "cap_ms": 8000.0},
}

# Per-gateway multiplicative latency offset -- modest, so gateways are distinguishable
# without any looking degraded at baseline.
GATEWAY_LATENCY_MULTIPLIER: dict[str, float] = {
    "gateway_A": 0.96,
    "gateway_B": 1.00,
    "gateway_C": 1.08,
    "gateway_D": 0.99,
    "gateway_E": 1.05,
}

# Aventum's synthetic response taxonomy. Deliberately UPPER_SNAKE and Aventum-specific:
# these are NOT real Razorpay/UPI/NPCI production error codes.
RESPONSE_APPROVED = "APPROVED"
RESPONSE_INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
RESPONSE_ISSUER_DECLINED = "ISSUER_DECLINED"
RESPONSE_PROCESSING_ERROR = "PROCESSING_ERROR"
RESPONSE_DO_NOT_HONOR = "DO_NOT_HONOR"
RESPONSE_TIMEOUT = "TIMEOUT"

RESPONSE_TAXONOMY: tuple[str, ...] = (
    RESPONSE_APPROVED,
    RESPONSE_INSUFFICIENT_FUNDS,
    RESPONSE_ISSUER_DECLINED,
    RESPONSE_PROCESSING_ERROR,
    RESPONSE_DO_NOT_HONOR,
    RESPONSE_TIMEOUT,
)

FAILURE_RESPONSES: tuple[str, ...] = (
    RESPONSE_INSUFFICIENT_FUNDS,
    RESPONSE_ISSUER_DECLINED,
    RESPONSE_PROCESSING_ERROR,
    RESPONSE_DO_NOT_HONOR,
    RESPONSE_TIMEOUT,
)

# Which side of the payment chain each failure family points at. This is what makes the
# response mix DIAGNOSTIC rather than decorative: an infrastructure-side degradation
# should later shift the infrastructure families, not the issuer-side ones.
RESPONSE_FAMILY_ATTRIBUTION: dict[str, str] = {
    RESPONSE_INSUFFICIENT_FUNDS: "issuer_side",
    RESPONSE_ISSUER_DECLINED: "issuer_side",
    RESPONSE_DO_NOT_HONOR: "issuer_side",
    RESPONSE_PROCESSING_ERROR: "infrastructure_side",
    RESPONSE_TIMEOUT: "infrastructure_side",
}

# Baseline share of FAILURES per response family, per gateway.
# DIRECT-ISH TRANSFER of the reference's roughly-even four-way decline split plus a
# small timeout tail, then tilted per gateway: gateways with a worse relative profile
# carry slightly more infrastructure-side attribution. That tilt is an Aventum modelling
# decision, not a reference measurement.
BASELINE_FAILURE_RESPONSE_MIX: dict[str, dict[str, float]] = {
    "gateway_A": {  # best relative profile -- most declines are issuer-side
        RESPONSE_INSUFFICIENT_FUNDS: 0.262,
        RESPONSE_ISSUER_DECLINED: 0.258,
        RESPONSE_DO_NOT_HONOR: 0.256,
        RESPONSE_PROCESSING_ERROR: 0.206,
        RESPONSE_TIMEOUT: 0.018,
    },
    "gateway_D": {
        RESPONSE_INSUFFICIENT_FUNDS: 0.253,
        RESPONSE_ISSUER_DECLINED: 0.250,
        RESPONSE_DO_NOT_HONOR: 0.247,
        RESPONSE_PROCESSING_ERROR: 0.228,
        RESPONSE_TIMEOUT: 0.022,
    },
    "gateway_B": {
        RESPONSE_INSUFFICIENT_FUNDS: 0.247,
        RESPONSE_ISSUER_DECLINED: 0.244,
        RESPONSE_DO_NOT_HONOR: 0.241,
        RESPONSE_PROCESSING_ERROR: 0.240,
        RESPONSE_TIMEOUT: 0.028,
    },
    "gateway_E": {
        RESPONSE_INSUFFICIENT_FUNDS: 0.240,
        RESPONSE_ISSUER_DECLINED: 0.237,
        RESPONSE_DO_NOT_HONOR: 0.235,
        RESPONSE_PROCESSING_ERROR: 0.254,
        RESPONSE_TIMEOUT: 0.034,
    },
    "gateway_C": {  # worst relative profile -- most infrastructure-side attribution
        RESPONSE_INSUFFICIENT_FUNDS: 0.233,
        RESPONSE_ISSUER_DECLINED: 0.230,
        RESPONSE_DO_NOT_HONOR: 0.228,
        RESPONSE_PROCESSING_ERROR: 0.271,
        RESPONSE_TIMEOUT: 0.038,
    },
}


@dataclass(frozen=True)
class GatewayFailureProfile:
    """A gateway's baseline failure characteristics, derived by calibration transfer."""

    gateway_id: str
    calibration_source_rail: str
    traffic_weight: float
    relative_failure_multiplier: float


def derive_relative_failure_multipliers() -> dict[str, float]:
    """
    Convert reference per-rail failure rates into DAMPED relative multipliers.

    Steps (all documented in docs/DAY2B_CALIBRATION_SPEC.md):
      1. Compute the reference's traffic-weighted mean failure rate.
      2. Express each rail as a ratio to that mean (its *relative* profile).
      3. Damp each ratio toward 1.0 by FAILURE_SPREAD_DAMPING (bounded transfer).

    The result is unitless: it says "this gateway fails ~1.26x the fleet average",
    never "this gateway fails 3.4% of the time in Nigeria".
    """
    weighted_mean = sum(
        REFERENCE_RAIL_FAILURE_RATE_PCT[rail] * REFERENCE_RAIL_TRAFFIC_SHARE[rail]
        for rail in REFERENCE_RAIL_FAILURE_RATE_PCT
    ) / sum(REFERENCE_RAIL_TRAFFIC_SHARE.values())

    multipliers: dict[str, float] = {}
    for gateway_id, rail in GATEWAY_CALIBRATION_SOURCE.items():
        raw_ratio = REFERENCE_RAIL_FAILURE_RATE_PCT[rail] / weighted_mean
        damped = 1.0 + FAILURE_SPREAD_DAMPING * (raw_ratio - 1.0)
        multipliers[gateway_id] = round(damped, 6)
    return multipliers


def build_gateway_failure_profiles() -> dict[str, GatewayFailureProfile]:
    multipliers = derive_relative_failure_multipliers()
    return {
        gateway_id: GatewayFailureProfile(
            gateway_id=gateway_id,
            calibration_source_rail=GATEWAY_CALIBRATION_SOURCE[gateway_id],
            traffic_weight=GATEWAY_TRAFFIC_WEIGHT[gateway_id],
            relative_failure_multiplier=multipliers[gateway_id],
        )
        for gateway_id in GATEWAY_CALIBRATION_SOURCE
    }


def absolute_failure_probabilities(observed_overall_failure_rate: float) -> dict[str, float]:
    """
    Anchor the relative profile to the OBSERVED failure rate of the canonical dataset.

    The absolute level is taken from observed UPI data, never from the calibration
    reference -- only the *shape* of the inter-gateway spread is transferred. The
    normalisation guarantees the traffic-weighted mean failure probability equals the
    observed rate exactly, so attaching synthetic gateways cannot distort the observed
    aggregate.
    """
    profiles = build_gateway_failure_profiles()
    weighted = sum(p.traffic_weight * p.relative_failure_multiplier for p in profiles.values())
    total_weight = sum(p.traffic_weight for p in profiles.values())
    # scale so sum(w_g * p_g) / sum(w_g) == observed_overall_failure_rate
    scale = observed_overall_failure_rate * total_weight / weighted
    return {
        gateway_id: profile.relative_failure_multiplier * scale
        for gateway_id, profile in profiles.items()
    }
