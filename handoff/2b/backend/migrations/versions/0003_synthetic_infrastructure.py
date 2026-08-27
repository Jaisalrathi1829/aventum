"""Day 2B synthetic payment-infrastructure baseline.

Creates ONLY the synthetic infrastructure entities needed for a normal-operation
baseline, plus a read surface that keeps observed and synthetic fields visibly
distinct. No canonical (Day 2A) table is altered.

Deliberately NOT created (later phases): incidents, incident_evidence, simulations,
simulation_results, recommendations, actions, verification_results, audit_events.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_HEALTH_STATES = "'DEGRADED', 'HEALTHY', 'UNAVAILABLE'"
_LATENCY_REGIMES = "'ELEVATED', 'NORMAL', 'TIMEOUT'"
_RESPONSE_CODES = (
    "'APPROVED', 'INSUFFICIENT_FUNDS', 'ISSUER_DECLINED', "
    "'PROCESSING_ERROR', 'DO_NOT_HONOR', 'TIMEOUT'"
)


def upgrade() -> None:
    # ------------------------------------------------ synthetic_generation_runs
    op.create_table(
        "synthetic_generation_runs",
        sa.Column("generation_run_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_ingestion_run_id", sa.BigInteger(), nullable=False),
        sa.Column("generation_seed", sa.Text(), nullable=False),
        sa.Column("generation_config_version", sa.Text(), nullable=False),
        sa.Column("synthetic_model_version", sa.Text(), nullable=False),
        sa.Column("routing_policy_version", sa.Text(), nullable=False),
        sa.Column("calibration_reference_name", sa.Text(), nullable=False),
        sa.Column("calibration_reference_version", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(12, 3), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rows_generated", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("generation_fingerprint", sa.String(64), nullable=True),
        sa.Column("observed_failure_rate", sa.Numeric(8, 6), nullable=True),
        sa.Column("model_parameters", postgresql.JSONB(), nullable=True),
        sa.Column("distribution_report", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("generation_run_id", name="pk_synthetic_generation_runs"),
        sa.ForeignKeyConstraint(["source_ingestion_run_id"],
                                ["ingestion_runs.ingestion_run_id"],
                                name="fk_synth_run_ingestion_run"),
        sa.CheckConstraint("is_synthetic = true", name="ck_synth_run_is_synthetic"),
        sa.CheckConstraint("status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SUPERSEDED')",
                           name="ck_synth_run_status"),
        sa.CheckConstraint("rows_generated >= 0", name="ck_synth_run_rows_generated"),
    )
    op.create_index("ix_synth_run_source_ingestion", "synthetic_generation_runs",
                    ["source_ingestion_run_id"])
    op.create_index("ix_synth_run_status", "synthetic_generation_runs", ["status"])

    # ------------------------------------------------------- synthetic_gateways
    op.create_table(
        "synthetic_gateways",
        sa.Column("gateway_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("calibration_source_rail", sa.Text(), nullable=True),
        sa.Column("calibration_reference_name", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("gateway_id", name="pk_synthetic_gateways"),
        sa.CheckConstraint("is_synthetic = true", name="ck_synth_gateway_is_synthetic"),
        sa.CheckConstraint("length(gateway_id) > 0", name="ck_synth_gateway_id_nonempty"),
    )

    # ----------------------------------------------- synthetic_gateway_profiles
    op.create_table(
        "synthetic_gateway_profiles",
        sa.Column("profile_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("gateway_id", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("baseline_traffic_weight", sa.Numeric(8, 6), nullable=False),
        sa.Column("relative_failure_multiplier", sa.Numeric(8, 6), nullable=False),
        sa.Column("baseline_failure_probability", sa.Numeric(8, 6), nullable=False),
        sa.Column("latency_multiplier", sa.Numeric(8, 6), nullable=False),
        sa.Column("failure_response_mix", postgresql.JSONB(), nullable=False),
        sa.Column("calibration_source_rail", sa.Text(), nullable=True),
        sa.Column("calibration_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("profile_id", name="pk_synthetic_gateway_profiles"),
        sa.ForeignKeyConstraint(["gateway_id"], ["synthetic_gateways.gateway_id"],
                                name="fk_synth_profile_gateway"),
        sa.UniqueConstraint("gateway_id", "profile_version",
                            name="uq_synth_profile_gateway_version"),
        sa.CheckConstraint("is_synthetic = true", name="ck_synth_profile_is_synthetic"),
        sa.CheckConstraint("baseline_traffic_weight > 0 AND baseline_traffic_weight <= 1",
                           name="ck_synth_profile_traffic_weight"),
        sa.CheckConstraint(
            "baseline_failure_probability >= 0 AND baseline_failure_probability < 1",
            name="ck_synth_profile_failure_probability"),
        sa.CheckConstraint("latency_multiplier > 0", name="ck_synth_profile_latency_multiplier"),
    )
    op.create_index("ix_synth_profile_version", "synthetic_gateway_profiles", ["profile_version"])

    # ----------------------------------------------- synthetic_routing_policies
    op.create_table(
        "synthetic_routing_policies",
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("selection_method", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("policy_version", name="pk_synthetic_routing_policies"),
        sa.CheckConstraint("is_synthetic = true", name="ck_synth_policy_is_synthetic"),
        sa.CheckConstraint("length(policy_version) > 0",
                           name="ck_synth_policy_version_nonempty"),
    )

    # -------------------------------------- synthetic_routing_policy_gateways
    op.create_table(
        "synthetic_routing_policy_gateways",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("gateway_id", sa.Text(), nullable=False),
        sa.Column("traffic_weight", sa.Numeric(8, 6), nullable=False),
        sa.Column("eligibility_conditions", postgresql.JSONB(), nullable=True),
        sa.Column("is_eligible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id", name="pk_synthetic_routing_policy_gateways"),
        sa.ForeignKeyConstraint(["policy_version"],
                                ["synthetic_routing_policies.policy_version"],
                                name="fk_synth_pg_policy"),
        sa.ForeignKeyConstraint(["gateway_id"], ["synthetic_gateways.gateway_id"],
                                name="fk_synth_pg_gateway"),
        sa.UniqueConstraint("policy_version", "gateway_id", name="uq_synth_pg_policy_gateway"),
        sa.CheckConstraint("is_synthetic = true", name="ck_synth_pg_is_synthetic"),
        sa.CheckConstraint("traffic_weight >= 0", name="ck_synth_pg_traffic_weight"),
    )

    # ------------------------------------ synthetic_gateway_health_states
    op.create_table(
        "synthetic_gateway_health_states",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("gateway_id", sa.Text(), nullable=False),
        sa.Column("generation_run_id", sa.BigInteger(), nullable=False),
        sa.Column("health_state", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_multiplier", sa.Numeric(8, 4), nullable=False,
                  server_default=sa.text("1.0")),
        sa.Column("latency_multiplier", sa.Numeric(8, 4), nullable=False,
                  server_default=sa.text("1.0")),
        sa.Column("timeout_multiplier", sa.Numeric(8, 4), nullable=False,
                  server_default=sa.text("1.0")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id", name="pk_synthetic_gateway_health_states"),
        sa.ForeignKeyConstraint(["gateway_id"], ["synthetic_gateways.gateway_id"],
                                name="fk_synth_health_gateway"),
        sa.ForeignKeyConstraint(["generation_run_id"],
                                ["synthetic_generation_runs.generation_run_id"],
                                name="fk_synth_health_generation_run", ondelete="CASCADE"),
        sa.CheckConstraint("is_synthetic = true", name="ck_synth_health_is_synthetic"),
        sa.CheckConstraint(f"health_state IN ({_HEALTH_STATES})", name="ck_synth_health_state"),
        sa.CheckConstraint("valid_to > valid_from", name="ck_synth_health_window"),
        sa.CheckConstraint("failure_multiplier > 0", name="ck_synth_health_failure_multiplier"),
        sa.CheckConstraint("latency_multiplier > 0", name="ck_synth_health_latency_multiplier"),
        sa.CheckConstraint("timeout_multiplier > 0", name="ck_synth_health_timeout_multiplier"),
    )
    op.create_index("ix_synth_health_gateway_window", "synthetic_gateway_health_states",
                    ["gateway_id", "valid_from", "valid_to"])
    op.create_index("ix_synth_health_generation_run", "synthetic_gateway_health_states",
                    ["generation_run_id"])

    # ------------------------------ synthetic_infrastructure_assignments
    op.create_table(
        "synthetic_infrastructure_assignments",
        sa.Column("assignment_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("source_ingestion_run_id", sa.BigInteger(), nullable=False),
        sa.Column("generation_run_id", sa.BigInteger(), nullable=False),
        sa.Column("routing_policy_version", sa.Text(), nullable=False),
        sa.Column("eligible_gateways", postgresql.JSONB(), nullable=False),
        sa.Column("selected_gateway_id", sa.Text(), nullable=False),
        sa.Column("selection_method", sa.Text(), nullable=False),
        sa.Column("selection_seed", sa.Text(), nullable=False),
        sa.Column("gateway_profile_version", sa.Text(), nullable=False),
        sa.Column("gateway_health_state", sa.Text(), nullable=False),
        sa.Column("latency_regime", sa.Text(), nullable=False),
        sa.Column("gateway_latency_ms", sa.Numeric(10, 2), nullable=False),
        sa.Column("gateway_response_code", sa.Text(), nullable=False),
        sa.Column("response_attribution", sa.Text(), nullable=False),
        sa.Column("modeled_failure_probability", sa.Numeric(8, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("assignment_id", name="pk_synthetic_infrastructure_assignments"),
        # ON DELETE CASCADE keeps Day 2A's wholesale re-ingestion working; the synthetic
        # population is wiped rather than silently orphaned. See the staleness policy in
        # docs/DAY2B_INFRASTRUCTURE_REPORT.md.
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.transaction_id"],
                                name="fk_synth_assignment_transaction", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_ingestion_run_id"],
                                ["ingestion_runs.ingestion_run_id"],
                                name="fk_synth_assignment_ingestion_run"),
        sa.ForeignKeyConstraint(["generation_run_id"],
                                ["synthetic_generation_runs.generation_run_id"],
                                name="fk_synth_assignment_generation_run", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["routing_policy_version"],
                                ["synthetic_routing_policies.policy_version"],
                                name="fk_synth_assignment_policy"),
        sa.ForeignKeyConstraint(["selected_gateway_id"], ["synthetic_gateways.gateway_id"],
                                name="fk_synth_assignment_gateway"),
        sa.UniqueConstraint("transaction_id", "generation_run_id",
                            name="uq_synth_assignment_txn_run"),
        sa.CheckConstraint("is_synthetic = true", name="ck_synth_assignment_is_synthetic"),
        sa.CheckConstraint(f"gateway_health_state IN ({_HEALTH_STATES})",
                           name="ck_synth_assignment_health_state"),
        sa.CheckConstraint(f"latency_regime IN ({_LATENCY_REGIMES})",
                           name="ck_synth_assignment_latency_regime"),
        sa.CheckConstraint("gateway_latency_ms > 0",
                           name="ck_synth_assignment_latency_positive"),
        sa.CheckConstraint(
            "modeled_failure_probability >= 0 AND modeled_failure_probability < 1",
            name="ck_synth_assignment_failure_probability"),
        sa.CheckConstraint(f"gateway_response_code IN ({_RESPONSE_CODES})",
                           name="ck_synth_assignment_response_code"),
        sa.CheckConstraint(
            "response_attribution IN ('approved', 'issuer_side', 'infrastructure_side')",
            name="ck_synth_assignment_response_attribution"),
        # Internal coherence, enforced by the database so an ETL bug cannot persist an
        # impossible combination (Day 2B §17).
        sa.CheckConstraint(
            "(gateway_response_code = 'TIMEOUT') = (latency_regime = 'TIMEOUT')",
            name="ck_synth_assignment_timeout_coherence"),
        # An APPROVED response must be attributed 'approved', and a declined response
        # must not be. Note this deliberately does NOT force APPROVED into the NORMAL
        # regime: a slow-but-successful payment is realistic, and forbidding it would
        # make latency a perfect predictor of outcome. The impossible case --
        # APPROVED with a TIMEOUT latency -- is already excluded by the timeout
        # coherence constraint above.
        sa.CheckConstraint(
            "(gateway_response_code = 'APPROVED' AND response_attribution = 'approved') "
            "OR (gateway_response_code <> 'APPROVED' AND response_attribution <> 'approved')",
            name="ck_synth_assignment_approved_coherence"),
    )
    op.create_index("ix_synth_assignment_transaction",
                    "synthetic_infrastructure_assignments", ["transaction_id"])
    op.create_index("ix_synth_assignment_generation_run",
                    "synthetic_infrastructure_assignments", ["generation_run_id"])
    op.create_index("ix_synth_assignment_source_ingestion",
                    "synthetic_infrastructure_assignments", ["source_ingestion_run_id"])
    op.create_index("ix_synth_assignment_gateway",
                    "synthetic_infrastructure_assignments", ["selected_gateway_id"])
    op.create_index("ix_synth_assignment_policy",
                    "synthetic_infrastructure_assignments", ["routing_policy_version"])

    # ----------------------------------------------------- read surface view
    # Column names carry their own provenance: `observed_*` came from the canonical UPI
    # dataset, `synthetic_*` was generated by Aventum's infrastructure model. A future
    # RCA/LLM tool consuming this view cannot confuse the two, which is the requirement
    # in docs/DAY2B_INTERFACE_CONTRACT.md §5.4.
    op.execute(
        """
        CREATE VIEW v_transaction_infrastructure AS
        SELECT
            t.transaction_id                     AS transaction_id,
            'OBSERVED'::text                     AS transaction_provenance,
            t.timestamp                          AS observed_timestamp,
            t.amount                             AS observed_amount,
            t.status                             AS observed_status,
            t.payment_method                     AS observed_payment_method,
            t.merchant_category                  AS observed_merchant_category,
            t.region                             AS observed_region,
            t.device                             AS observed_device,
            t.network                            AS observed_network,
            t.sender_bank                        AS observed_sender_bank,
            t.receiver_bank                      AS observed_receiver_bank,
            t.issuer_bank                        AS observed_issuer_bank,
            t.fraud_flag                         AS observed_fraud_flag,
            t.source_dataset                     AS observed_source_dataset,
            t.ingestion_run_id                   AS observed_ingestion_run_id,
            'SYNTHETIC'::text                    AS infrastructure_provenance,
            a.is_synthetic                       AS infrastructure_is_synthetic,
            a.selected_gateway_id                AS synthetic_gateway_id,
            a.routing_policy_version             AS synthetic_routing_policy_version,
            a.selection_method                   AS synthetic_selection_method,
            a.eligible_gateways                  AS synthetic_eligible_gateways,
            a.gateway_profile_version            AS synthetic_gateway_profile_version,
            a.gateway_health_state               AS synthetic_gateway_health_state,
            a.latency_regime                     AS synthetic_latency_regime,
            a.gateway_latency_ms                 AS synthetic_gateway_latency_ms,
            a.gateway_response_code              AS synthetic_gateway_response_code,
            a.response_attribution               AS synthetic_response_attribution,
            a.modeled_failure_probability        AS synthetic_modeled_failure_probability,
            a.generation_run_id                  AS synthetic_generation_run_id,
            a.source_ingestion_run_id            AS synthetic_source_ingestion_run_id
        FROM transactions t
        JOIN synthetic_infrastructure_assignments a
          ON a.transaction_id = t.transaction_id
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_transaction_infrastructure")
    op.drop_table("synthetic_infrastructure_assignments")
    op.drop_table("synthetic_gateway_health_states")
    op.drop_table("synthetic_routing_policy_gateways")
    op.drop_table("synthetic_routing_policies")
    op.drop_table("synthetic_gateway_profiles")
    op.drop_table("synthetic_gateways")
    op.drop_table("synthetic_generation_runs")
