"""
Day 4A counterfactual vocabulary and bounds.

Everything a reviewer would want to argue about -- how large a reroute may be, how small
a cohort is too small to project from, what counts as an invalid simulation -- is a
named constant here rather than a literal buried in a query.

These are SIMULATION bounds. The action-safety thresholds that decide whether a
simulated candidate may be recommended live in `aventum_policy.constants`, deliberately
in a different package: the simulator's job is to answer "what would happen", and the
policy engine's job is to answer "are we allowed to do it". Keeping the two sets apart
is what stops a simulator tweak from quietly loosening a safety gate.
"""

from __future__ import annotations

# ------------------------------------------------------------------ action taxonomy
ACTION_NO_ACTION = "NO_ACTION"
ACTION_REROUTE = "REROUTE"
ACTION_TYPES = (ACTION_NO_ACTION, ACTION_REROUTE)

# ------------------------------------------------------------------ statuses
STATUS_VALID = "VALID"
STATUS_INVALID = "SIMULATION_INVALID"
SIMULATION_STATUSES = (STATUS_INVALID, STATUS_VALID)

# ------------------------------------------------------------------ candidate set
# The bounded reroute percentages evaluated for every eligible healthy target.
#
# 30% is a POLICY CEILING, not a claim that 30% is optimal. It is the largest shift that
# keeps a receiving gateway below the concentration cap in the flagship scenario
# (gateway_C carries 13% of baseline traffic, so 30% of it is ~3.9 percentage points
# landing on a peer). It is a documented prototype bound, derived from concentration --
# NOT from capacity, which does not exist in this data at all.
CANDIDATE_TRAFFIC_PERCENTAGES = (10.0, 20.0, 30.0)

# Hard ceiling enforced by the simulator itself. A request above this is INVALID rather
# than silently clamped: silently clamping would make the returned projection describe a
# different action than the one asked for.
MAX_TRAFFIC_PERCENTAGE = 30.0

# ------------------------------------------------------------------ validity bounds
# Below this, the affected cohort is too small for a projection to mean anything. Set to
# match Day 3's MIN_COHORT_SIZE so a cohort large enough to diagnose is exactly the set
# large enough to simulate -- one threshold, not two that could drift apart.
MIN_COHORT_SIZE = 100

# ------------------------------------------------------------------ invalid reasons
# Structured, machine-readable. Every one of these is returned INSTEAD of a number.
INVALID_COHORT_EMPTY = "COHORT_EMPTY"
INVALID_COHORT_TOO_SMALL = "COHORT_BELOW_MIN_SIZE"
INVALID_NO_ELIGIBLE_TARGET = "NO_ELIGIBLE_TARGET"
INVALID_TARGET_NOT_ELIGIBLE = "TARGET_NOT_ELIGIBLE"
INVALID_TARGET_NOT_HEALTHY = "TARGET_NOT_HEALTHY"
INVALID_TARGET_NO_HEALTH_RECORD = "TARGET_NO_HEALTH_RECORD"
INVALID_TARGET_UNKNOWN = "TARGET_UNKNOWN"
INVALID_TRAFFIC_EXCEEDS_MAX = "TRAFFIC_EXCEEDS_MAXIMUM"
INVALID_INCIDENT_UNRESOLVED = "INCIDENT_UNRESOLVED"
INVALID_ANALYSIS_RUN_UNRESOLVED = "ANALYSIS_RUN_UNRESOLVED"
INVALID_NO_DAY3_SIMULATION = "NO_DAY3_SIMULATION"
INVALID_SOURCE_EQUALS_TARGET = "SOURCE_EQUALS_TARGET"
INVALID_NO_SOURCE_GATEWAY = "NO_SOURCE_GATEWAY"

INVALID_REASONS = (
    INVALID_ANALYSIS_RUN_UNRESOLVED,
    INVALID_COHORT_EMPTY,
    INVALID_COHORT_TOO_SMALL,
    INVALID_INCIDENT_UNRESOLVED,
    INVALID_NO_DAY3_SIMULATION,
    INVALID_NO_ELIGIBLE_TARGET,
    INVALID_NO_SOURCE_GATEWAY,
    INVALID_SOURCE_EQUALS_TARGET,
    INVALID_TARGET_NOT_ELIGIBLE,
    INVALID_TARGET_NOT_HEALTHY,
    INVALID_TARGET_NO_HEALTH_RECORD,
    INVALID_TARGET_UNKNOWN,
    INVALID_TRAFFIC_EXCEEDS_MAX,
)

# ------------------------------------------------------------------ health vocabulary
HEALTH_HEALTHY = "HEALTHY"

# ------------------------------------------------------------------ provenance labels
# Applied to every value the simulator publishes, so a downstream reader can always tell
# measured history from modelled infrastructure from modelled incident outcome.
PROVENANCE_OBSERVED = "OBSERVED"
PROVENANCE_SYNTHETIC = "SYNTHETIC"
PROVENANCE_SIMULATED = "SIMULATED"
PROVENANCE_ASSUMED = "ASSUMED"
PROVENANCE_UNAVAILABLE = "UNAVAILABLE"

# ------------------------------------------------------------------ honesty markers
# Capacity does not exist anywhere in Day 2B (no throughput ceiling on any profile,
# policy, or health row). It is reported UNAVAILABLE, never estimated from traffic
# weight -- an invented capacity figure is exactly the fabricated production value the
# project's honesty rules forbid. Concentration is the binding allocation constraint.
CAPACITY_UNAVAILABLE = "UNAVAILABLE"

# `eligibility_conditions` is NULL for all five gateways under `baseline-v1`, so
# eligibility is currently unconditional. Reporting a richer eligibility model than the
# data supports would imply a check that never happened.
ELIGIBILITY_UNCONDITIONAL = "ELIGIBILITY_UNCONDITIONAL"

# ------------------------------------------------------------------ versions
BASELINE_PROFILE_VERSION = "baseline-v1"
BASELINE_POLICY_VERSION = "baseline-v1"
