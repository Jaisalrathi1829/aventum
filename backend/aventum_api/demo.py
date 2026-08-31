"""
Deterministic demo reset (§30).

WHAT IT CLEARS, AND WHAT IT REFUSES TO TOUCH
--------------------------------------------
It truncates exactly the tables that hold *workflow* state produced during a demo run:
verifications, audit events, actions, approvals, recommendations, counterfactual
simulations, and the agent's own run/tool-call records.

It does NOT touch, and has no statement capable of touching:

    transactions                    the 250,000 observed rows
    synthetic_*                     the generated infrastructure baseline
    incidents, incident_*           Day 3 analysis, evidence, RCA, ground truth
    dataset_registry, ingestion_*   provenance of the canonical load

That boundary is the whole point. A reset that could alter the canonical dataset would
invalidate the canonical fingerprint, and a demo tool capable of doing that is more
dangerous than a demo that has to be reloaded by hand.

The allow-list below is explicit rather than derived. Deriving "everything Day 4 owns"
from metadata would silently widen the moment someone adds a table.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

# Order matters: children before parents, so foreign keys never block the truncate.
# TRUNCATE ... CASCADE is deliberately NOT used -- cascading is exactly how a reset
# reaches a table nobody intended it to reach.
WORKFLOW_TABLES = (
    "verifications",
    "audit_events",
    "actions",
    "approvals",
    "recommendations",
    "counterfactual_simulations",
    "agent_tool_calls",
    "agent_runs",
)

# Named so a test can assert the reset never learned how to write to them.
PROTECTED_TABLES = (
    "transactions",
    "incidents",
    "incident_analysis_runs",
    "incident_anomalies",
    "incident_evidence",
    "incident_rca_results",
    "incident_hypotheses",
    "incident_ground_truth",
    "incident_simulation_runs",
    "simulated_incident_outcomes",
    "synthetic_gateways",
    "synthetic_gateway_profiles",
    "synthetic_gateway_health_states",
    "synthetic_generation_runs",
    "synthetic_infrastructure_assignments",
    "synthetic_routing_policies",
    "synthetic_routing_policy_gateways",
    "dataset_registry",
    "ingestion_runs",
    "ingestion_rejects",
    "transactions_staging",
    "banks",
)


def reset_demo_state(session: Session) -> dict:
    """
    Clear workflow state and report exactly what was cleared.

    Returns counts taken BEFORE the truncate so the caller can see what was removed,
    plus a verification that the protected dataset is intact afterwards.
    """
    before = {
        table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        for table in WORKFLOW_TABLES
    }

    # RESTART IDENTITY so a reset demo produces the same IDs as a fresh one -- a judge
    # re-running the flagship should see recommendation 1, not recommendation 47.
    session.execute(
        text(
            "TRUNCATE TABLE "
            + ", ".join(WORKFLOW_TABLES)
            + " RESTART IDENTITY"
        )
    )
    session.flush()

    after = {
        table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        for table in WORKFLOW_TABLES
    }
    observed_rows = session.execute(text("SELECT count(*) FROM transactions")).scalar()
    incidents = session.execute(text("SELECT count(*) FROM incidents")).scalar()

    return {
        "reset": True,
        "cleared": before,
        "remaining": after,
        "preserved": {
            "transactions": observed_rows,
            "incidents": incidents,
            "note": (
                "Observed transactions, the synthetic baseline and all Day 3 incident "
                "analysis are untouched by design."
            ),
        },
    }
