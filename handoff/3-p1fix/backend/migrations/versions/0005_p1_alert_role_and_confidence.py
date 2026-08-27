"""Day 3 P1 fixes: causal alert roles (P1-1) and action-safety confidence fields (P1-2).

Additive only. No existing column is altered or dropped, no data is rewritten, and no
Day 1/2 table is touched.

P1-1 — `incident_anomalies` gains a causal role. A single degradation lights up every
cohort that intersects it; those cohorts are statistically genuine but causally
DERIVATIVE, and must not reach an operator (or Day 4) as independent actionable causes.
`derived_from_cohort_key` records which stronger cohort explains them and `independence`
records how much of their own movement survived that cohort's exclusion.

P1-2 — `incident_rca_results` gains `severity`, `significance_sigma` and
`evidence_strength` alongside the existing `confidence`, so absolute evidence strength
travels separately from attribution quality and no single confidence scalar can
authorise a larger intervention on its own. `incident_hypotheses` gains the same
strength value per hypothesis for auditability.

Existing rows are backfilled to the additive-safe defaults: every prior anomaly becomes
PRIMARY (which is what the pre-fix system effectively asserted about all of them), and
prior RCA rows get severity 'NONE' with zero strength, which is honest — those values
were not computed at the time.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_ALERT_ROLES = "'DERIVATIVE', 'PRIMARY'"


def upgrade() -> None:
    # ------------------------------------------------ P1-1: causal alert roles
    op.add_column(
        "incident_anomalies",
        sa.Column("alert_role", sa.Text(), nullable=False, server_default=sa.text("'PRIMARY'")),
    )
    op.add_column(
        "incident_anomalies", sa.Column("derived_from_cohort_key", sa.Text(), nullable=True)
    )
    op.add_column(
        "incident_anomalies", sa.Column("derived_from_anomaly_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "incident_anomalies",
        sa.Column("independence", sa.Numeric(precision=9, scale=6), nullable=True),
    )
    op.create_check_constraint(
        "ck_anomaly_alert_role", "incident_anomalies", f"alert_role IN ({_ALERT_ROLES})"
    )
    # A derivative alert must name what explains it; a primary must not claim a parent.
    op.create_check_constraint(
        "ck_anomaly_role_parent_coherent",
        "incident_anomalies",
        "(alert_role = 'DERIVATIVE') = (derived_from_cohort_key IS NOT NULL)",
    )
    op.create_index(
        "ix_anomaly_run_role", "incident_anomalies", ["analysis_run_id", "alert_role"]
    )

    # ---------------------------------------- P1-2: action-safety RCA fields
    op.add_column(
        "incident_rca_results",
        sa.Column("severity", sa.Text(), nullable=False, server_default=sa.text("'NONE'")),
    )
    op.add_column(
        "incident_rca_results",
        sa.Column(
            "significance_sigma",
            sa.Numeric(precision=12, scale=4),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "incident_rca_results",
        sa.Column(
            "evidence_strength",
            sa.Numeric(precision=6, scale=4),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "incident_hypotheses",
        sa.Column(
            "evidence_strength",
            sa.Numeric(precision=6, scale=4),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("incident_hypotheses", "evidence_strength")
    op.drop_column("incident_rca_results", "evidence_strength")
    op.drop_column("incident_rca_results", "significance_sigma")
    op.drop_column("incident_rca_results", "severity")

    op.drop_index("ix_anomaly_run_role", table_name="incident_anomalies")
    op.drop_constraint(
        "ck_anomaly_role_parent_coherent", "incident_anomalies", type_="check"
    )
    op.drop_constraint("ck_anomaly_alert_role", "incident_anomalies", type_="check")
    op.drop_column("incident_anomalies", "independence")
    op.drop_column("incident_anomalies", "derived_from_anomaly_id")
    op.drop_column("incident_anomalies", "derived_from_cohort_key")
    op.drop_column("incident_anomalies", "alert_role")
