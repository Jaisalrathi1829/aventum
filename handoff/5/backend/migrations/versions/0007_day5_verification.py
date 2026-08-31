"""Day 5 verification: independent post-action recovery measurement.

Adds ONE table, `verifications`. Additive only: no ALTER, no DROP, and nothing in Day 1,
Day 2, Day 3 or Day 4 is touched. `transactions` remains read-only, the canonical
fingerprint is unaffected, and every Day 4 table keeps its exact Day 4 shape.

WHY A SEPARATE TABLE RATHER THAN COLUMNS ON `actions`
------------------------------------------------------
Verification must be able to disagree with the action that produced it. Storing the
verdict on the action row would put the measurement and the thing being measured in the
same record, under the same writer, which is precisely the independence the Day 4A
handoff was designed to preserve when it recorded
`"recovery_claim": "NONE — Day 5 owns verification"`.

STRUCTURAL PROPERTIES ENFORCED HERE, NOT IN APPLICATION CODE
-------------------------------------------------------------
1. `uq_verification_identity` — UNIQUE on `(action_id, model_version)`. Re-verifying an
   action under the same verifier converges on the stored verdict instead of minting a
   second opinion a UI could then choose between. Two concurrent verification requests
   cannot both insert.

2. `ck_verification_is_simulated` — `is_simulated NOT NULL DEFAULT true CHECK (= true)`,
   following the Day 2B/Day 3/Day 4A precedent. The database itself refuses to record a
   verification of a real execution, because no real execution exists.

3. `ck_verification_outcome_coherent` — a COMPLETE verification must carry a verdict and
   an INELIGIBLE one must not. The pairing cannot drift, so a row can never present as
   "verified" while holding no outcome.

4. `ck_verification_ineligible_coherent` — the mirror rule for the reason string.

`outcome` is CHECK-constrained to the three-value vocabulary, so
`RECOVERY_NOT_VERIFIED` is a first-class persisted result rather than an absence.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verifications",
        sa.Column("verification_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("action_id", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("recommendation_id", sa.BigInteger(), nullable=True),
        sa.Column("simulation_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("ineligible_reason", sa.Text(), nullable=True),
        # ---- the baseline the action started from -------------------------------
        sa.Column("baseline_failure_rate", sa.Numeric(9, 6), nullable=True),
        sa.Column("baseline_success_rate", sa.Numeric(9, 6), nullable=True),
        sa.Column("baseline_gmv_at_risk", sa.Numeric(18, 2), nullable=True),
        # ---- what the simulation PROJECTED --------------------------------------
        sa.Column("projected_success_delta", sa.Numeric(9, 6), nullable=True),
        sa.Column("projected_gmv_retained", sa.Numeric(18, 2), nullable=True),
        # ---- what the adapter ACTUALLY modelled. Never merged with the above. ----
        sa.Column("actual_failure_rate", sa.Numeric(9, 6), nullable=True),
        sa.Column("actual_success_rate", sa.Numeric(9, 6), nullable=True),
        sa.Column("actual_gmv_at_risk", sa.Numeric(18, 2), nullable=True),
        # ---- what verification concluded ----------------------------------------
        sa.Column("measured_success_delta", sa.Numeric(9, 6), nullable=True),
        sa.Column("measured_failure_rate_improvement", sa.Numeric(9, 6), nullable=True),
        sa.Column("actual_gmv_recovered", sa.Numeric(18, 2), nullable=True),
        sa.Column("variance_vs_projection", sa.Numeric(9, 6), nullable=True),
        sa.Column("attainment_ratio", sa.Numeric(9, 6), nullable=True),
        sa.Column("transactions_moved", sa.BigInteger(), nullable=True),
        sa.Column("population", sa.BigInteger(), nullable=True),
        sa.Column("integrity_passed", sa.Boolean(), nullable=False),
        sa.Column("integrity_checks", postgresql.JSONB(), nullable=True),
        sa.Column("cohort_definition", postgresql.JSONB(), nullable=True),
        sa.Column("measurement_window", postgresql.JSONB(), nullable=True),
        sa.Column("metric_definitions", postgresql.JSONB(), nullable=True),
        sa.Column("reasons", postgresql.JSONB(), nullable=True),
        sa.Column("limitations", postgresql.JSONB(), nullable=True),
        sa.Column("verification_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=False),
        sa.Column(
            "is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("verification_id", name="pk_verifications"),
        sa.ForeignKeyConstraint(
            ["action_id"], ["actions.action_id"], name="fk_verification_action"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.incident_id"], name="fk_verification_incident"
        ),
        sa.UniqueConstraint("action_id", "model_version", name="uq_verification_identity"),
        sa.CheckConstraint("is_simulated = true", name="ck_verification_is_simulated"),
        sa.CheckConstraint(
            "status IN ('COMPLETE', 'INELIGIBLE')", name="ck_verification_status"
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('RECOVERY_EFFECTIVE', 'PARTIALLY_EFFECTIVE', 'RECOVERY_NOT_VERIFIED')",
            name="ck_verification_outcome",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETE') = (outcome IS NOT NULL)",
            name="ck_verification_outcome_coherent",
        ),
        sa.CheckConstraint(
            "(status = 'INELIGIBLE') = (ineligible_reason IS NOT NULL)",
            name="ck_verification_ineligible_coherent",
        ),
    )
    op.create_index("ix_verification_action", "verifications", ["action_id"])
    op.create_index("ix_verification_incident", "verifications", ["incident_id"])


def downgrade() -> None:
    op.drop_index("ix_verification_incident", table_name="verifications")
    op.drop_index("ix_verification_action", table_name="verifications")
    op.drop_table("verifications")
