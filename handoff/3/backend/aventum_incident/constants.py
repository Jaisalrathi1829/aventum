"""
Day 3 vocabulary and analytical thresholds.

Everything a reviewer would want to argue about -- what counts as a big enough cohort,
how many sigma is "significant", what severity band a score falls into -- is a named
constant here rather than a literal buried in a query, so the detection policy can be
read in one place and changed in one place.
"""

from __future__ import annotations

# --------------------------------------------------------------- incident lifecycle
# CREATED  -- definition persisted, nothing simulated yet
# ACTIVE   -- simulated outcomes generated for the window
# DETECTED -- an anomaly detector found the affected population
# DIAGNOSED-- RCA produced a root-cause conclusion
# RESOLVED -- reserved for Day 4/5 (an action was taken); Day 3 never sets it
# VERIFIED -- reserved for Day 5 (post-action verification); Day 3 never sets it
INCIDENT_STATUSES = (
    "CREATED",
    "ACTIVE",
    "DETECTED",
    "DIAGNOSED",
    "RESOLVED",
    "VERIFIED",
)

# The forward-only lifecycle. Day 3 drives CREATED -> ACTIVE -> DETECTED -> DIAGNOSED.
INCIDENT_STATUS_ORDER = {status: index for index, status in enumerate(INCIDENT_STATUSES)}

# Incident taxonomy. Deliberately broader than the flagship scenario so the hypothesis
# engine has genuinely competing categories to rank rather than one foregone conclusion.
INCIDENT_TYPES = (
    "gateway_degradation",
    "issuer_degradation",
    "payment_method_degradation",
    "network_segment_degradation",
    "systemic_degradation",
)

# ------------------------------------------------------------------- run status
RUN_STATUSES = ("RUNNING", "SUCCEEDED", "FAILED", "SUPERSEDED")

# ------------------------------------------------------------------ source layers
# Which epistemic layer an evidence value came from. Never flattened: a reader must
# always be able to tell measured-history from modelled-infrastructure from
# modelled-incident-outcome. See docs/DAY2B_TRUTH_MODEL.md.
SOURCE_LAYER_OBSERVED = "OBSERVED"      # layer 1 -- canonical transactions
SOURCE_LAYER_SYNTHETIC = "SYNTHETIC"    # layers 2-3 -- Day 2B infrastructure/signals
SOURCE_LAYER_SIMULATED = "SIMULATED"    # Day 3 incident-period modelled outcomes
SOURCE_LAYERS = (SOURCE_LAYER_OBSERVED, SOURCE_LAYER_SYNTHETIC, SOURCE_LAYER_SIMULATED)

# ------------------------------------------------------------------ cohort dimensions
# The single dimensions the detector scans. These map to columns available on the
# observed transaction or its Day 2B infrastructure assignment.
DIMENSION_GATEWAY = "gateway"
DIMENSION_SENDER_BANK = "sender_bank"
DIMENSION_PAYMENT_METHOD = "payment_method"
DIMENSION_REGION = "region"
DIMENSION_DEVICE = "device"
DIMENSION_NETWORK = "network"

SINGLE_DIMENSIONS = (
    DIMENSION_GATEWAY,
    DIMENSION_SENDER_BANK,
    DIMENSION_PAYMENT_METHOD,
    DIMENSION_REGION,
    DIMENSION_DEVICE,
    DIMENSION_NETWORK,
)

# Intersections worth scanning. Kept to an explicit allow-list rather than a full power
# set: the power set over six dimensions is 63 combinations whose cell counts collapse
# below any usable sample size, which is how multi-dimensional detectors end up
# reporting noise with great confidence.
INTERSECTION_DIMENSIONS = (
    (DIMENSION_GATEWAY, DIMENSION_SENDER_BANK),
    (DIMENSION_GATEWAY, DIMENSION_PAYMENT_METHOD),
    (DIMENSION_GATEWAY, DIMENSION_SENDER_BANK, DIMENSION_PAYMENT_METHOD),
)

# SQL expression for each dimension, against the analysis CTE's column names.
DIMENSION_SQL = {
    DIMENSION_GATEWAY: "gateway_id",
    DIMENSION_SENDER_BANK: "sender_bank",
    DIMENSION_PAYMENT_METHOD: "payment_method",
    DIMENSION_REGION: "region",
    DIMENSION_DEVICE: "device",
    DIMENSION_NETWORK: "network",
}

# --------------------------------------------------------------- detection thresholds
# Minimum transactions in the incident-window cohort before it may be scored at all.
# Without this, a 3-transaction cohort that happens to fail twice reads as a 67%
# failure rate and outranks a genuine gateway-wide degradation.
MIN_COHORT_SIZE = 100

# Minimum transactions required in the BASELINE cohort for its rate to be a usable
# comparison point.
MIN_BASELINE_COHORT_SIZE = 300

# Minimum absolute failure-rate increase (percentage points, as a fraction) before a
# cohort is considered anomalous regardless of how significant the statistics look.
# A 0.4pp move on an enormous cohort can be statistically significant and operationally
# meaningless; significance alone is not evidence of an incident.
MIN_ABSOLUTE_RATE_DELTA = 0.02

# Two-proportion z threshold below which a cohort is not reported at all.
MIN_SIGNIFICANCE_SIGMA = 3.0

# Failure-rate increase (as a fraction) treated as a fully-weighted effect when scoring.
# The anomaly score multiplies statistical significance by how much of this the cohort
# actually moved, so an enormous cohort with a 0.3pp shift cannot outrank a real outage
# on statistical strength alone.
ANOMALY_SCORE_REFERENCE_DELTA = 0.15

# Severity bands, keyed on the two-proportion z score. Ordered high to low.
SEVERITY_BANDS = (
    ("CRITICAL", 9.0),
    ("HIGH", 6.0),
    ("MEDIUM", 4.0),
    ("LOW", 3.0),
)
SEVERITY_NONE = "NONE"

# Severities that count as "high severity" for the no-incident false-positive test.
HIGH_SEVERITIES = ("CRITICAL", "HIGH")

# When a broader cohort and a narrower cohort inside it tell the same story, the
# narrower one is suppressed unless it is meaningfully stronger. Without this a single
# gateway incident produces one alert per (gateway x bank x method) cell it touches.
DUPLICATE_SUPPRESSION_SIGMA_MARGIN = 2.0

# ------------------------------------------------------------------- RCA thresholds
# Confidence floors for the RCA verdict. Below UNCERTAIN, RCA declines to name a cause.
RCA_CONFIDENT_THRESHOLD = 0.60
RCA_UNCERTAIN_THRESHOLD = 0.35

RCA_VERDICT_CONFIDENT = "CONFIDENT"
RCA_VERDICT_UNCERTAIN = "UNCERTAIN"
RCA_VERDICT_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
RCA_VERDICTS = (RCA_VERDICT_CONFIDENT, RCA_VERDICT_UNCERTAIN, RCA_VERDICT_INSUFFICIENT)

# ------------------------------------------------------------------- evidence types
EVIDENCE_FAILURE_RATE = "failure_rate"
EVIDENCE_LATENCY = "latency"
EVIDENCE_RESPONSE_MIX = "response_mix"
EVIDENCE_CONTROL_COMPARISON = "control_comparison"
EVIDENCE_BLAST_RADIUS = "blast_radius"
EVIDENCE_TEMPORAL = "temporal_alignment"
EVIDENCE_GMV = "gmv_impact"
# Does this cohort's anomaly survive removing the leading suspect on another dimension?
# This is what separates a cause from its shadow -- see evidence.py.
EVIDENCE_CONFOUNDING = "confounding_check"

EVIDENCE_TYPES = (
    EVIDENCE_FAILURE_RATE,
    EVIDENCE_LATENCY,
    EVIDENCE_RESPONSE_MIX,
    EVIDENCE_CONTROL_COMPARISON,
    EVIDENCE_BLAST_RADIUS,
    EVIDENCE_TEMPORAL,
    EVIDENCE_GMV,
    EVIDENCE_CONFOUNDING,
)

# Below this share of its original movement, a cohort's anomaly is considered explained
# away by the confounder rather than independent.
INDEPENDENCE_COLLAPSE_THRESHOLD = 0.35

# ---------------------------------------------------------------- hypothesis types
HYPOTHESIS_TYPES = (
    "gateway_degradation",
    "issuer_degradation",
    "payment_method_degradation",
    "network_segment_degradation",
    "systemic_degradation",
)

# Which cohort dimension is the "subject" of each hypothesis category. Used to turn a
# ranked anomaly on some dimension into a candidate explanation.
HYPOTHESIS_SUBJECT_DIMENSION = {
    "gateway_degradation": DIMENSION_GATEWAY,
    "issuer_degradation": DIMENSION_SENDER_BANK,
    "payment_method_degradation": DIMENSION_PAYMENT_METHOD,
    "network_segment_degradation": DIMENSION_NETWORK,
    "systemic_degradation": None,
}

# Response codes that indicate the infrastructure rather than the issuer declined.
# A degradation of payment infrastructure should shift the mix toward these; an issuer
# problem should not. This is what separates the two hypotheses evidentially.
INFRASTRUCTURE_SIDE_RESPONSES = ("PROCESSING_ERROR", "TIMEOUT")
