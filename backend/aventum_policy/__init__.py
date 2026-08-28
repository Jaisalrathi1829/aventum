"""
Aventum deterministic safety policy (Day 4A).

The only thing standing between a simulated projection and a human being asked to
approve an action.

THIS PACKAGE IS THE AUTHORITY ON WHAT IS ALLOWED
-------------------------------------------------
Thresholds are module constants. They are not arguments, not configuration, not columns,
and not fields on any payload -- so no recommendation, no approval request, and (in Day
4B) no agent can influence them. The only way to change a threshold is to edit
`constants.py` and bump `POLICY_VERSION`, which invalidates every recommendation
validated under the old one.

FAIL-CLOSED, AND INTERPRETABLE
------------------------------
Thirteen explicit gates, each returning PASS or FAIL with a machine-readable reason
code. All must pass. There is no blended score, no weighting, and no threshold anywhere
that can be traded off against another -- because the failure mode Day 3's P1-2 fix
identified was exactly that: one strong signal masking a weak one when they are summed.
Confidence AND evidence strength AND significance AND severity are required together.

Capacity is deliberately NOT a gate. No capacity telemetry exists in this dataset, so a
capacity gate would be theatre -- it would report a check that never happened.
Concentration is the binding allocation constraint, and it is derivable from real
traffic share.
"""

__version__ = "0.1.0"

# Bumped when any gate, threshold, or evaluation rule changes. Stamped onto every
# recommendation and re-checked at execution: a bump correctly refuses to execute an
# action validated under different rules.
POLICY_VERSION = "1.0.0"
