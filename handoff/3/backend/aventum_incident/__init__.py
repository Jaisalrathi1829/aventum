"""
Aventum incident intelligence (Day 3).

Implements exactly one chain:

    inject incident -> generate simulated outcomes -> detect anomaly
        -> collect evidence -> rank hypotheses -> produce explainable RCA

APPROACH B (binding, docs/DAY3_IMPLEMENTATION_CONTRACT.md)
----------------------------------------------------------
`transactions` is never written to. An incident does NOT redistribute observed
historical failures between gateways -- that construction was measured to drop the
healthy control group to ~0.48x its baseline rate at 25% severity, making healthy
gateways look *healthier* during an incident and biasing RCA evaluation optimistically.

Instead a degradation ADDS modelled failures to the affected cohort in a separate,
separately-provenanced simulated-outcome layer:

    observed transaction (immutable)
        + Day 2B synthetic infrastructure
        + Day 3 incident state
        -> simulated_incident_outcomes  (its own table, its own `simulated_*` naming)

Non-claims (binding, see docs/DAY2B_TRUTH_MODEL.md):
  - An injected incident is a SYNTHETIC construction. It never occurred in history.
  - A simulated outcome is a MODELLED outcome, never a measured one.
  - No value here is real Razorpay telemetry, routing, or incident data.

Ground-truth isolation: the known-by-construction cause of an injected incident lives
in its own table (`incident_ground_truth`) which no detection/evidence/hypothesis/RCA
code path imports or queries. It is read only by the evaluation layer, after diagnosis
has already been produced.

Scope boundary: this package ends at RCA. It contains no counterfactual simulator, no
recommendation engine, no approval workflow, no execution, no LLM/agent integration,
and no frontend -- those are Day 4 and Day 5.
"""

__version__ = "0.1.0"

# Bumped when the incident/simulation generative model changes structurally in a way
# that would produce different simulated outcomes from identical inputs.
INCIDENT_MODEL_VERSION = "1.0.0"

# Bumped when incident CONFIGURATION semantics change (multiplier meaning, window
# semantics, cohort vocabulary) without necessarily changing model structure.
INCIDENT_CONFIG_VERSION = "1.0.0"

# Bumped when the detection/evidence/hypothesis/RCA analytical model changes in a way
# that would produce different scores or rankings from identical inputs.
ANALYSIS_MODEL_VERSION = "1.0.0"
