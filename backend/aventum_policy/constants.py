"""
Action-safety thresholds. System-owned, immutable at runtime.

Every value here is a MODULE CONSTANT, reachable only by editing this file. None is a
function parameter, a database column, a config key, or a field on any payload. That is
the mechanism, not the aspiration: `aventum_policy.gate` reads these names directly, so
there is no argument a caller could pass that would move one.

WHERE EACH NUMBER COMES FROM
----------------------------
The four evidence thresholds are inherited from Day 3, not invented here:
`MIN_CONFIDENCE` is Day 3's own `RCA_CONFIDENT_THRESHOLD`, and severity/alert-role
vocabularies are Day 3's. Requiring all four TOGETHER is the direct consumption of Day
3's P1-2 fix -- the whole point of that fix was that no single scalar may authorise an
intervention.
"""

from __future__ import annotations

# ------------------------------------------------------- Day 3 evidence quartet
# All four are required simultaneously. This is P1-2 enforced at the action boundary.
REQUIRED_RCA_VERDICT = "CONFIDENT"
MIN_CONFIDENCE = 0.60          # matches Day 3's RCA_CONFIDENT_THRESHOLD exactly
MIN_EVIDENCE_STRENGTH = 0.50
MIN_SIGNIFICANCE_SIGMA = 6.0
ALLOWED_SEVERITIES = ("CRITICAL", "HIGH")

# Only a PRIMARY alert is an actionable root cause. A DERIVATIVE alert is a causal
# shadow of a stronger cohort; acting on one is precisely the defect Day 3's P1-1 fix
# closed, and Day 4 must not re-open it.
REQUIRED_ALERT_ROLE = "PRIMARY"

# ------------------------------------------------------- action bounds
# Largest share of the affected cohort that may be moved. A documented prototype bound
# derived from concentration headroom -- NOT from capacity, which does not exist here.
MAX_TRAFFIC_SHIFT_PERCENTAGE = 30.0

# Largest share of ALL in-window traffic any single gateway may hold after the action.
# This is the binding allocation constraint in Day 4, standing in for the capacity check
# that the data cannot support.
MAX_CONCENTRATION_AFTER = 0.40

# Minimum expected GMV retained (INR, same units as `transactions.amount`) required
# before acting is preferred to doing nothing. See `optimize.DEFAULT_NO_ACTION_MARGIN`
# for the derivation; re-exported here because deciding whether a benefit is "enough" is
# a policy judgement, not a simulator one.
NO_ACTION_MARGIN = 1000.0

# ------------------------------------------------------- lifetimes
# A recommendation describes a world that is still moving; 30 minutes is long enough for
# a human to read and decide, short enough that the world has probably not moved under it.
RECOMMENDATION_TTL_MINUTES = 30

# Deliberately SHORTER than the recommendation TTL: an approval is a judgement about a
# CURRENT world state, so it should expire before the thing it approves.
APPROVAL_TTL_MINUTES = 15

# ------------------------------------------------------- reason codes
# Machine-readable, one per gate. Persisted on the recommendation and emitted as audit
# events, so a blocked action is explainable without re-running anything.
RCA_NOT_CONFIDENT = "RCA_NOT_CONFIDENT"
CONFIDENCE_BELOW_THRESHOLD = "CONFIDENCE_BELOW_THRESHOLD"
EVIDENCE_STRENGTH_BELOW_THRESHOLD = "EVIDENCE_STRENGTH_BELOW_THRESHOLD"
SIGNIFICANCE_BELOW_THRESHOLD = "SIGNIFICANCE_BELOW_THRESHOLD"
SEVERITY_BELOW_THRESHOLD = "SEVERITY_BELOW_THRESHOLD"
ALERT_NOT_PRIMARY = "ALERT_NOT_PRIMARY"
SIMULATION_INVALID = "SIMULATION_INVALID"
STALE_SIMULATION = "STALE_SIMULATION"
TARGET_NOT_ELIGIBLE = "TARGET_NOT_ELIGIBLE"
TARGET_NOT_HEALTHY = "TARGET_NOT_HEALTHY"
TRAFFIC_SHIFT_EXCEEDS_BOUND = "TRAFFIC_SHIFT_EXCEEDS_BOUND"
CONCENTRATION_EXCEEDS_BOUND = "CONCENTRATION_EXCEEDS_BOUND"
BENEFIT_BELOW_NO_ACTION_MARGIN = "BENEFIT_BELOW_NO_ACTION_MARGIN"

# Execution-time rejection codes (contract §8). Distinct from gate codes because they
# describe a world that CHANGED after validation, not a candidate that never qualified.
RECOMMENDATION_NOT_APPROVED = "RECOMMENDATION_NOT_APPROVED"
RECOMMENDATION_EXPIRED = "RECOMMENDATION_EXPIRED"
APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
APPROVAL_NOT_APPROVED = "APPROVAL_NOT_APPROVED"
APPROVAL_FINGERPRINT_MISMATCH = "APPROVAL_FINGERPRINT_MISMATCH"
POLICY_REVALIDATION_FAILED = "POLICY_REVALIDATION_FAILED"
POLICY_VERSION_CHANGED = "POLICY_VERSION_CHANGED"
INCIDENT_NO_LONGER_ACTIVE = "INCIDENT_NO_LONGER_ACTIVE"
DUPLICATE_EXECUTION = "DUPLICATE_EXECUTION"
RECOMMENDATION_NOT_FOUND = "RECOMMENDATION_NOT_FOUND"
APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"

ALL_REASON_CODES = (
    ALERT_NOT_PRIMARY,
    APPROVAL_EXPIRED,
    APPROVAL_FINGERPRINT_MISMATCH,
    APPROVAL_NOT_APPROVED,
    APPROVAL_NOT_FOUND,
    BENEFIT_BELOW_NO_ACTION_MARGIN,
    CONCENTRATION_EXCEEDS_BOUND,
    CONFIDENCE_BELOW_THRESHOLD,
    DUPLICATE_EXECUTION,
    EVIDENCE_STRENGTH_BELOW_THRESHOLD,
    INCIDENT_NO_LONGER_ACTIVE,
    POLICY_REVALIDATION_FAILED,
    POLICY_VERSION_CHANGED,
    RCA_NOT_CONFIDENT,
    RECOMMENDATION_EXPIRED,
    RECOMMENDATION_NOT_APPROVED,
    RECOMMENDATION_NOT_FOUND,
    SEVERITY_BELOW_THRESHOLD,
    SIGNIFICANCE_BELOW_THRESHOLD,
    SIMULATION_INVALID,
    STALE_SIMULATION,
    TARGET_NOT_ELIGIBLE,
    TARGET_NOT_HEALTHY,
    TRAFFIC_SHIFT_EXCEEDS_BOUND,
)

# ------------------------------------------------------- policy results
RESULT_PERMITTED = "PERMITTED"
RESULT_BLOCKED = "BLOCKED"
