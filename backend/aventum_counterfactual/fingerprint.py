"""
Derived fingerprints — the staleness mechanism.

WHY THIS IS A DERIVED CHECK, NOT A STATUS COLUMN
-------------------------------------------------
`input_fingerprint` is recomputed from the CURRENT world every time it is checked: once
when the simulation is created, again when a recommendation is built, and again at
execution. It is never read back from a flag someone could set.

That distinction is the whole defense. A status column saying "still fresh" can be
edited, defaulted, or left stale by a bug. A hash over the actual inputs cannot: if the
incident window moved, a health state changed, a profile was re-versioned, or the cohort
gained or lost a transaction, the recomputed value simply differs, and the action is
rejected. There is no code path that can assert freshness without re-deriving it.

WHAT GOES INTO IT
-----------------
Everything the counterfactual claims to hold constant (contract §5). If a variable is
held constant but absent from this hash, a change to it would go undetected — so the
membership of this function IS the enforcement of the held-constant list.
"""

from __future__ import annotations

import hashlib

from . import COUNTERFACTUAL_CONFIG_VERSION, COUNTERFACTUAL_MODEL_VERSION
from .source import WorldState

# Field separator. Part of the determinism contract: changing it changes every
# fingerprint and invalidates every stored simulation, so it must not be edited casually.
_SEP = "|"


def _digest_transaction_set(world: WorldState) -> str:
    """
    SHA-256 over the sorted (transaction_id, amount, observed_status) triples.

    Amount and observed status travel with the id deliberately. Hashing ids alone would
    miss a canonical row being altered underneath the simulation — which must never
    happen (`transactions` is immutable) but which this fingerprint should still catch
    if it ever did, rather than trusting the invariant it is meant to protect.
    """
    digest = hashlib.sha256()
    for txn in sorted(world.transactions, key=lambda t: t.transaction_id):
        digest.update(
            f"{txn.transaction_id}{_SEP}{txn.amount:.2f}{_SEP}{txn.observed_status}\n".encode()
        )
    return digest.hexdigest()


def _digest_profiles(world: WorldState) -> str:
    """SHA-256 over the sorted gateway profile parameters actually in force."""
    digest = hashlib.sha256()
    for gateway_id in sorted(world.profiles):
        p = world.profiles[gateway_id]
        mix = ",".join(f"{k}={p.failure_response_mix[k]:.6f}" for k in sorted(p.failure_response_mix))
        digest.update(
            (
                f"{p.gateway_id}{_SEP}{p.profile_version}{_SEP}"
                f"{p.baseline_failure_probability:.6f}{_SEP}{p.latency_multiplier:.6f}{_SEP}"
                f"{p.baseline_traffic_weight:.6f}{_SEP}{mix}\n"
            ).encode()
        )
    return digest.hexdigest()


def _digest_health(world: WorldState) -> str:
    """SHA-256 over every health window overlapping the incident window."""
    digest = hashlib.sha256()
    for gateway_id in sorted(world.health):
        for w in sorted(world.health[gateway_id], key=lambda x: (x.valid_from, x.valid_to)):
            # Only windows that intersect the incident window can affect the projection.
            if w.valid_to <= world.window_start or w.valid_from >= world.window_end:
                continue
            digest.update(
                (
                    f"{w.gateway_id}{_SEP}{w.health_state}{_SEP}"
                    f"{w.valid_from.isoformat()}{_SEP}{w.valid_to.isoformat()}{_SEP}"
                    f"{w.failure_multiplier:.4f}{_SEP}{w.latency_multiplier:.4f}{_SEP}"
                    f"{w.timeout_multiplier:.4f}\n"
                ).encode()
            )
    return digest.hexdigest()


def _digest_eligibility(world: WorldState) -> str:
    digest = hashlib.sha256()
    for gateway_id in sorted(world.eligibility):
        e = world.eligibility[gateway_id]
        digest.update(
            (
                f"{e.gateway_id}{_SEP}{e.policy_version}{_SEP}{int(e.is_eligible)}{_SEP}"
                f"{e.traffic_weight:.6f}{_SEP}{e.conditions}\n"
            ).encode()
        )
    return digest.hexdigest()


def compute_input_fingerprint(world: WorldState, seed: str, policy_version: str) -> str:
    """
    SHA-256 over every held-constant input to a counterfactual.

    Deliberately independent of the candidate: the same world yields the same
    fingerprint for NO_ACTION and for a 30% reroute, so a whole candidate sweep shares
    one freshness token and staleness is a property of the WORLD, not of one option.
    Candidate identity is carried separately by `candidate_key`, and the pair
    (candidate_key, input_fingerprint) is what makes a simulation idempotent.
    """
    parts = [
        str(world.incident_id),
        world.incident_key,
        world.window_start.isoformat(),
        world.window_end.isoformat(),
        f"{world.failure_multiplier:.4f}",
        f"{world.latency_multiplier:.4f}",
        f"{world.timeout_multiplier:.4f}",
        world.affected_gateway_id or "",
        # Sorted so an equal segment dict never hashes two different ways.
        str(sorted((world.affected_segment or {}).items())),
        _digest_transaction_set(world),
        _digest_profiles(world),
        _digest_health(world),
        _digest_eligibility(world),
        policy_version,
        COUNTERFACTUAL_MODEL_VERSION,
        COUNTERFACTUAL_CONFIG_VERSION,
        seed,
    ]
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()


def compute_simulation_fingerprint(rendered_rows: list[str]) -> str:
    """SHA-256 over the ordered, deterministically rendered projected outcomes."""
    digest = hashlib.sha256()
    for line in rendered_rows:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def compute_recommendation_fingerprint(fields: dict) -> str:
    """
    SHA-256 over the decision-relevant recommendation content.

    An approval binds to this value, so editing any field below after a human approved
    it produces a mismatch and the execution is rejected. That is what stops an approval
    from being transferable to a modified recommendation.
    """
    rendered = _SEP.join(f"{k}={fields[k]}" for k in sorted(fields))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def compute_idempotency_key(recommendation_id: int, approval_id: int, adapter_name: str) -> str:
    """
    SHA256(recommendation_id || approval_id || adapter_name) — contract §7.

    Backed by a UNIQUE constraint on `actions`, so duplicate execution is prevented by
    PostgreSQL rather than by application logic that a race could slip past.
    """
    payload = f"{recommendation_id}{_SEP}{approval_id}{_SEP}{adapter_name}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_execution_fingerprint(action_id_parts: list[str]) -> str:
    """SHA-256 over the ordered execution result fields."""
    return hashlib.sha256(_SEP.join(action_id_parts).encode("utf-8")).hexdigest()
