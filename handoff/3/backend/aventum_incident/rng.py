"""
Deterministic pseudo-random streams for incident simulation.

Reuses Day 2B's primitives (`lane_uniform`, `weighted_choice`, `lognormal_from_uniform`)
rather than reimplementing them, so both layers share one audited determinism contract:
SHA-256 only, never Python's salted `hash()`, never an unseeded `random`/`numpy.random`,
never wall-clock time, never database row order.

WHY A SEPARATE DIGEST RATHER THAN Day 2B's LANE_RESERVED
---------------------------------------------------------
Day 2B reserved one lane (bytes 24-31 of the per-transaction digest) for incident-time
draws. That would supply a single uniform per transaction. Day 3 needs three
independent draws per transaction (does this outcome flip, which response family, what
latency), and -- more importantly -- the draws must change when the INCIDENT DEFINITION
changes, not only when the transaction changes.

So Day 3 derives its own digest keyed on the incident as well as the transaction:

    transaction_id | incident_key | simulation_seed | model_version | config_version

This gives four fresh disjoint lanes, and guarantees the property the contract requires:
altering the seed, the window, the target, or any multiplier alters `incident_key`,
which alters every draw, which alters the simulation fingerprint. Day 2B's LANE_RESERVED
is left genuinely unused and still available.
"""

from __future__ import annotations

import hashlib

from aventum_synth.rng import lane_uniform, lognormal_from_uniform, weighted_choice

__all__ = [
    "LANE_OUTCOME",
    "LANE_RESPONSE",
    "LANE_LATENCY",
    "LANE_SPARE",
    "incident_assignment_key",
    "incident_digest_for",
    "lane_uniform",
    "weighted_choice",
    "lognormal_from_uniform",
]

# Lane assignments over the Day 3 digest. Disjoint by construction, so deciding whether
# an outcome flips cannot perturb the latency draw.
LANE_OUTCOME = 0    # bytes 0-7   -- does this transaction's outcome change
LANE_RESPONSE = 1   # bytes 8-15  -- which failure response family
LANE_LATENCY = 2    # bytes 16-23 -- latency magnitude
LANE_SPARE = 3      # bytes 24-31 -- unused, reserved for later incident mechanics


def incident_assignment_key(
    transaction_id: str,
    incident_key: str,
    simulation_seed: str,
    incident_model_version: str,
    incident_config_version: str,
) -> str:
    """
    The canonical key string identifying one simulated draw.

    Field order and the '|' separator are part of the determinism contract: changing
    either changes every simulated outcome, so they must not be edited casually.
    """
    return (
        f"{transaction_id}|{incident_key}|{simulation_seed}|"
        f"{incident_model_version}|{incident_config_version}"
    )


def incident_digest_for(
    transaction_id: str,
    incident_key: str,
    simulation_seed: str,
    incident_model_version: str,
    incident_config_version: str,
) -> bytes:
    key = incident_assignment_key(
        transaction_id,
        incident_key,
        simulation_seed,
        incident_model_version,
        incident_config_version,
    )
    return hashlib.sha256(key.encode("utf-8")).digest()
