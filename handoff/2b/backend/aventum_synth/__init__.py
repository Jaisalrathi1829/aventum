"""
Aventum synthetic payment-infrastructure model (Day 2B).

This package generates the infrastructure dimension the public UPI dataset does not
contain -- gateway identity, routing decision, latency, response/error code, gateway
health -- as an EXPLICITLY SYNTHETIC layer attached to canonical observed transactions.

Non-claims (binding, see docs/DAY2B_TRUTH_MODEL.md):
  - These are NOT real Razorpay gateways, routing decisions, latencies, or error codes.
  - These are NOT observed UPI gateway logs.
  - The Nigerian Card Payment Dataset is a synthetic CALIBRATION REFERENCE, not
    production telemetry, and is never joined to a UPI transaction.
  - A generated value is a modeled signal, never a measured historical outcome.

Scope boundary: this package ends at a reproducible NORMAL-OPERATION baseline. It
contains no incident injection, anomaly detection, RCA, simulation, recommendation,
approval, execution, or verification logic.
"""

__version__ = "0.1.0"

# Bumped when the generative model's structure changes in a way that would produce
# different synthetic values from identical inputs.
SYNTHETIC_MODEL_VERSION = "1.0.0"

# Bumped when gateway/routing/calibration CONFIGURATION changes (weights, profiles,
# taxonomy) without necessarily changing model structure.
GENERATION_CONFIG_VERSION = "1.0.0"
