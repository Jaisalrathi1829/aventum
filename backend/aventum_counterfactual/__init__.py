"""
Aventum counterfactual simulation (Day 4A).

Answers exactly one question, deterministically:

    What would the incident-window outcomes have been if TRAFFIC ALLOCATION had been
    different, with the incident, the population, and every model parameter held fixed?

THE ONE-VARIABLE RULE
---------------------
A counterfactual is only interpretable if exactly one thing changed. This package
changes traffic allocation and nothing else, and it audits itself: every simulation row
persists `held_constant` and `changed_variables`, so a reader can check the claim rather
than trust it. If a controlled comparison cannot be constructed, the simulation returns
`SIMULATION_INVALID` with a structured reason -- never a fabricated projection.

ONE AUTHORITATIVE PROBABILITY MODEL
-----------------------------------
There is no Day 4 failure model. `P_success(transaction | gateway, incident state)` is
computed by Day 2B's `GatewayRuntimeProfile.effective_failure_probability`, reached
through Day 3's `build_runtime_profile()`, and outcomes are regenerated through Day 2B's
`generate_signals()` keyed by Day 3's `incident_digest_for()`. Day 4 re-uses that funnel
verbatim; it does not re-derive it. A second formula that happened to agree today would
silently diverge the first time either side changed.

That reuse is what makes the counterfactual comparable to the Day 3 baseline at all:
NO_ACTION reproduces Day 3's simulated outcomes exactly, so every candidate is measured
against a real simulated baseline rather than an assumed one.

APPROACH B STILL HOLDS
----------------------
An observed FAILED transaction stays FAILED under every candidate policy. A reroute may
prevent a MODELLED incident-induced failure; it may never rewrite history.
`transactions` is read-only here, as it is everywhere.

Non-claims: every projection in this package is a MODELLED outcome over a SYNTHETIC
infrastructure world under a SYNTHETIC incident. No figure here is measured production
behaviour, and no GMV figure is "recovered" -- only projected, estimated, or at risk.
"""

__version__ = "0.1.0"

# Bumped when the counterfactual model changes structurally in a way that would produce
# different projections from identical inputs. Stamped into `input_fingerprint`, so a
# bump correctly invalidates every existing simulation.
COUNTERFACTUAL_MODEL_VERSION = "1.0.0"

# Bumped when candidate-set semantics or simulation configuration change without
# necessarily changing the model structure.
COUNTERFACTUAL_CONFIG_VERSION = "1.0.0"
