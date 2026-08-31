"""
Day 5 verification vocabulary and thresholds.

These thresholds belong to VERIFICATION and to nothing else. They are deliberately not
imported from `aventum_policy` or from the recommendation layer: verification exists to
form an opinion about whether an action helped, and an opinion that borrows its
standards from the layer that proposed the action is not independent of it.

The asymmetry is intentional. `RECOVERY_EFFECTIVE` requires the measured improvement to
be real AND to have substantially met what was projected; everything short of that
degrades to `PARTIALLY_EFFECTIVE`, and an action that did not measurably improve the
cohort is `RECOVERY_NOT_VERIFIED`. There is no threshold at which a bad outcome is
rounded up to a good one.
"""

from __future__ import annotations

VERIFICATION_MODEL_VERSION = "day5-verification-v1"

# ------------------------------------------------------------------ outcome vocabulary
RECOVERY_EFFECTIVE = "RECOVERY_EFFECTIVE"
PARTIALLY_EFFECTIVE = "PARTIALLY_EFFECTIVE"
RECOVERY_NOT_VERIFIED = "RECOVERY_NOT_VERIFIED"

VERIFICATION_OUTCOMES = (
    RECOVERY_EFFECTIVE,
    PARTIALLY_EFFECTIVE,
    RECOVERY_NOT_VERIFIED,
)

# ------------------------------------------------------------------ status vocabulary
VERIFICATION_COMPLETE = "COMPLETE"
VERIFICATION_INELIGIBLE = "INELIGIBLE"

VERIFICATION_STATUSES = (VERIFICATION_COMPLETE, VERIFICATION_INELIGIBLE)

# ------------------------------------------------------------------ thresholds
# A failure-rate improvement smaller than this is treated as indistinguishable from no
# effect. Expressed in absolute failure-rate points (0.0025 = 0.25pp).
MIN_MEANINGFUL_FAILURE_RATE_IMPROVEMENT = 0.0025

# Fraction of the PROJECTED success improvement that must actually be attained before
# the result may be called effective rather than partial.
ATTAINMENT_EFFECTIVE = 0.80

# Below this share of the projection the action is reported as not verified even if the
# raw movement happened to be positive: a projection missed by more than this margin
# means the model that authorised the action did not describe reality.
ATTAINMENT_FLOOR = 0.20

# ------------------------------------------------------------------ integrity checks
CHECK_SIMULATION_LINEAGE = "SIMULATION_LINEAGE"
CHECK_EXECUTION_FINGERPRINT = "EXECUTION_FINGERPRINT"
CHECK_COHORT_PRESENT = "COHORT_DEFINITION_PRESENT"
CHECK_WINDOW_PRESENT = "MEASUREMENT_WINDOW_PRESENT"
CHECK_BASELINE_PRESENT = "PRE_ACTION_BASELINE_PRESENT"
CHECK_OUTCOME_PRESENT = "ACTUAL_OUTCOME_PRESENT"
CHECK_POPULATION_STABLE = "POPULATION_STABLE"

INTEGRITY_CHECKS = (
    CHECK_SIMULATION_LINEAGE,
    CHECK_EXECUTION_FINGERPRINT,
    CHECK_COHORT_PRESENT,
    CHECK_WINDOW_PRESENT,
    CHECK_BASELINE_PRESENT,
    CHECK_OUTCOME_PRESENT,
    CHECK_POPULATION_STABLE,
)

# ------------------------------------------------------------------ provenance
VERIFICATION_PROVENANCE = "SYNTHETIC_INCIDENT / SIMULATED_EXECUTION / DETERMINISTIC_VERIFICATION"

RECOVERY_CLAIM_NOTE = (
    "No production money was recovered. This measures a MODELLED post-action population "
    "against a MODELLED pre-action baseline, both derived from observed transaction "
    "amounts under a synthetic incident."
)
