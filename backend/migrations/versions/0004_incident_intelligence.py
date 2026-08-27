"""Day 3 incident intelligence: injection, Approach B simulated outcomes, detection, evidence, RCA.

Adds nine tables. Touches no existing table: `transactions` and every Day 2B
`synthetic_*` table are read-only from Day 3's perspective, and this migration does not
alter, drop, or add a column to any of them.

Provenance is machine-enforced throughout, following the Day 2B precedent -- every table
carries a boolean flag with `NOT NULL DEFAULT true CHECK (flag = true)`, so PostgreSQL
itself refuses to let this data be relabelled as observed fact.

The Approach B guarantee is likewise a database constraint, not a convention:
`ck_simulated_outcome_approach_b_no_rescue` makes it impossible to store a row in which
an observed FAILED transaction was simulated as a SUCCESS. The rejected Approach A
(reallocating observed failures between gateways) is therefore structurally
unrepresentable rather than merely discouraged in documentation.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_INCIDENT_STATUSES = "'ACTIVE', 'CREATED', 'DETECTED', 'DIAGNOSED', 'RESOLVED', 'VERIFIED'"
_INCIDENT_TYPES = (
    "'gateway_degradation', 'issuer_degradation', 'network_segment_degradation', "
    "'payment_method_degradation', 'systemic_degradation'"
)
_RUN_STATUSES = "'FAILED', 'RUNNING', 'SUCCEEDED', 'SUPERSEDED'"
_SOURCE_LAYERS = "'OBSERVED', 'SIMULATED', 'SYNTHETIC'"
_EVIDENCE_TYPES = (
    "'blast_radius', 'confounding_check', 'control_comparison', 'failure_rate', "
    "'gmv_impact', 'latency', 'response_mix', 'temporal_alignment'"
)
_HYPOTHESIS_TYPES = (
    "'gateway_degradation', 'issuer_degradation', 'network_segment_degradation', "
    "'payment_method_degradation', 'systemic_degradation'"
)
_RCA_VERDICTS = "'CONFIDENT', 'INSUFFICIENT_EVIDENCE', 'UNCERTAIN'"


def upgrade() -> None:
    # ------------------------------------------------------------------ incidents
    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_key", sa.String(length=64), nullable=False),
        sa.Column("incident_name", sa.Text(), nullable=False),
        sa.Column("incident_type", sa.Text(), nullable=False),
        sa.Column("affected_gateway_id", sa.Text(), nullable=True),
        sa.Column("affected_segment", postgresql.JSONB(), nullable=True),
        sa.Column("incident_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("incident_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_multiplier", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("latency_multiplier", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("timeout_multiplier", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("target_failure_rate", sa.Numeric(precision=8, scale=6), nullable=True),
        sa.Column("generation_run_id", sa.BigInteger(), nullable=False),
        sa.Column("source_ingestion_run_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'CREATED'")),
        sa.Column("incident_seed", sa.Text(), nullable=False),
        sa.Column("incident_model_version", sa.Text(), nullable=False),
        sa.Column("incident_config_version", sa.Text(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("incident_id", name="pk_incidents"),
        sa.UniqueConstraint("incident_key", name="uq_incidents_key"),
        sa.ForeignKeyConstraint(
            ["affected_gateway_id"], ["synthetic_gateways.gateway_id"], name="fk_incident_gateway"
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["synthetic_generation_runs.generation_run_id"],
            name="fk_incident_generation_run",
        ),
        sa.ForeignKeyConstraint(
            ["source_ingestion_run_id"],
            ["ingestion_runs.ingestion_run_id"],
            name="fk_incident_ingestion_run",
        ),
        sa.CheckConstraint("is_synthetic = true", name="ck_incident_is_synthetic"),
        sa.CheckConstraint(f"status IN ({_INCIDENT_STATUSES})", name="ck_incident_status"),
        sa.CheckConstraint(f"incident_type IN ({_INCIDENT_TYPES})", name="ck_incident_type"),
        sa.CheckConstraint("incident_end > incident_start", name="ck_incident_window"),
        sa.CheckConstraint("failure_multiplier > 0", name="ck_incident_failure_multiplier"),
        sa.CheckConstraint("latency_multiplier > 0", name="ck_incident_latency_multiplier"),
        sa.CheckConstraint("timeout_multiplier > 0", name="ck_incident_timeout_multiplier"),
    )
    op.create_index("ix_incidents_window", "incidents", ["incident_start", "incident_end"])
    op.create_index("ix_incidents_gateway", "incidents", ["affected_gateway_id"])
    op.create_index("ix_incidents_status", "incidents", ["status"])

    # ------------------------------------------------------- incident_ground_truth
    # Isolated on purpose. No detection, evidence, hypothesis, or RCA code path names
    # this table -- see aventum_incident/models.py for the full rationale.
    op.create_table(
        "incident_ground_truth",
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("ground_truth_root_cause", sa.Text(), nullable=False),
        sa.Column("ground_truth_gateway_id", sa.Text(), nullable=True),
        sa.Column("ground_truth_detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "is_evaluation_only", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("incident_id", name="pk_incident_ground_truth"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_ground_truth_incident",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("is_evaluation_only = true", name="ck_ground_truth_evaluation_only"),
    )

    # ---------------------------------------------------- incident_simulation_runs
    op.create_table(
        "incident_simulation_runs",
        sa.Column("simulation_run_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("simulation_seed", sa.Text(), nullable=False),
        sa.Column("incident_model_version", sa.Text(), nullable=False),
        sa.Column("incident_config_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'RUNNING'")),
        sa.Column("rows_in_window", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_simulated", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_changed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("simulation_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("model_parameters", postgresql.JSONB(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("simulation_run_id", name="pk_incident_simulation_runs"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_simulation_run_incident",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("is_simulated = true", name="ck_simulation_run_is_simulated"),
        sa.CheckConstraint(f"status IN ({_RUN_STATUSES})", name="ck_simulation_run_status"),
        sa.CheckConstraint("rows_simulated >= 0", name="ck_simulation_run_rows"),
        sa.CheckConstraint("rows_changed >= 0", name="ck_simulation_run_changed"),
        sa.CheckConstraint(
            "rows_changed <= rows_simulated", name="ck_simulation_run_changed_bounded"
        ),
    )
    op.create_index("ix_simulation_runs_incident", "incident_simulation_runs", ["incident_id"])

    # ------------------------------------------------ simulated_incident_outcomes
    op.create_table(
        "simulated_incident_outcomes",
        sa.Column("simulated_outcome_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("simulation_run_id", sa.BigInteger(), nullable=False),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("gateway_id", sa.Text(), nullable=False),
        sa.Column("observed_status", sa.Text(), nullable=False),
        sa.Column("simulated_status", sa.Text(), nullable=False),
        sa.Column("simulated_response_code", sa.Text(), nullable=False),
        sa.Column("simulated_response_attribution", sa.Text(), nullable=False),
        sa.Column("simulated_latency_regime", sa.Text(), nullable=False),
        sa.Column("simulated_latency_ms", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("outcome_changed", sa.Boolean(), nullable=False),
        sa.Column(
            "modeled_failure_probability", sa.Numeric(precision=8, scale=6), nullable=False
        ),
        sa.Column("in_affected_cohort", sa.Boolean(), nullable=False),
        sa.Column("source_ingestion_run_id", sa.BigInteger(), nullable=False),
        sa.Column("generation_run_id", sa.BigInteger(), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("simulated_outcome_id", name="pk_simulated_incident_outcomes"),
        sa.UniqueConstraint(
            "simulation_run_id", "transaction_id", name="uq_simulated_outcome_run_transaction"
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_simulated_outcome_incident",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_run_id"],
            ["incident_simulation_runs.simulation_run_id"],
            name="fk_simulated_outcome_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.transaction_id"],
            name="fk_simulated_outcome_transaction",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["gateway_id"], ["synthetic_gateways.gateway_id"], name="fk_simulated_outcome_gateway"
        ),
        sa.ForeignKeyConstraint(
            ["source_ingestion_run_id"],
            ["ingestion_runs.ingestion_run_id"],
            name="fk_simulated_outcome_ingestion_run",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["synthetic_generation_runs.generation_run_id"],
            name="fk_simulated_outcome_generation_run",
        ),
        sa.CheckConstraint("is_simulated = true", name="ck_simulated_outcome_is_simulated"),
        sa.CheckConstraint(
            "observed_status IN ('SUCCESS', 'FAILED')", name="ck_simulated_outcome_observed_status"
        ),
        sa.CheckConstraint(
            "simulated_status IN ('SUCCESS', 'FAILED')",
            name="ck_simulated_outcome_simulated_status",
        ),
        sa.CheckConstraint("simulated_latency_ms > 0", name="ck_simulated_outcome_latency_positive"),
        sa.CheckConstraint(
            "(simulated_status = 'SUCCESS') = (simulated_response_code = 'APPROVED')",
            name="ck_simulated_outcome_status_response_coherent",
        ),
        sa.CheckConstraint(
            "(simulated_response_code = 'TIMEOUT') = (simulated_latency_regime = 'TIMEOUT')",
            name="ck_simulated_outcome_timeout_coherent",
        ),
        sa.CheckConstraint(
            "outcome_changed = (simulated_status <> observed_status)",
            name="ck_simulated_outcome_changed_coherent",
        ),
        # APPROACH B, ENFORCED BY THE DATABASE.
        sa.CheckConstraint(
            "NOT (observed_status = 'FAILED' AND simulated_status = 'SUCCESS')",
            name="ck_simulated_outcome_approach_b_no_rescue",
        ),
    )
    op.create_index(
        "ix_simulated_outcome_incident", "simulated_incident_outcomes", ["incident_id"]
    )
    op.create_index(
        "ix_simulated_outcome_transaction", "simulated_incident_outcomes", ["transaction_id"]
    )
    op.create_index(
        "ix_simulated_outcome_gateway",
        "simulated_incident_outcomes",
        ["incident_id", "gateway_id"],
    )

    # ------------------------------------------------------ incident_analysis_runs
    op.create_table(
        "incident_analysis_runs",
        sa.Column("analysis_run_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("analysis_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysis_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysis_model_version", sa.Text(), nullable=False),
        sa.Column("detection_config", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'RUNNING'")),
        sa.Column("cohorts_scanned", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("anomalies_found", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("analysis_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("detection_ms", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("evidence_ms", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("rca_ms", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("analysis_run_id", name="pk_incident_analysis_runs"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_analysis_run_incident",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("is_synthetic = true", name="ck_analysis_run_is_synthetic"),
        sa.CheckConstraint(f"status IN ({_RUN_STATUSES})", name="ck_analysis_run_status"),
        sa.CheckConstraint(
            "analysis_window_end > analysis_window_start", name="ck_analysis_run_window"
        ),
        sa.CheckConstraint(
            "baseline_window_end > baseline_window_start", name="ck_analysis_run_baseline_window"
        ),
    )
    op.create_index("ix_analysis_runs_incident", "incident_analysis_runs", ["incident_id"])

    # ----------------------------------------------------------- incident_anomalies
    op.create_table(
        "incident_anomalies",
        sa.Column("anomaly_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("cohort_key", sa.Text(), nullable=False),
        sa.Column("cohort_dimensions", postgresql.JSONB(), nullable=False),
        sa.Column("cohort_definition", postgresql.JSONB(), nullable=False),
        sa.Column("cohort_depth", sa.Integer(), nullable=False),
        sa.Column("detection_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detection_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("affected_population", sa.Integer(), nullable=False),
        sa.Column("baseline_population", sa.Integer(), nullable=False),
        sa.Column("baseline_metrics", postgresql.JSONB(), nullable=False),
        sa.Column("current_metrics", postgresql.JSONB(), nullable=False),
        sa.Column("baseline_failure_rate", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("current_failure_rate", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("absolute_delta", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("relative_delta", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("significance_sigma", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("anomaly_score", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("gmv_total", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("gmv_at_risk", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("suppressed_by", sa.Text(), nullable=True),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("anomaly_id", name="pk_incident_anomalies"),
        sa.UniqueConstraint("analysis_run_id", "cohort_key", name="uq_anomaly_run_cohort"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["incident_analysis_runs.analysis_run_id"],
            name="fk_anomaly_analysis_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_anomaly_incident",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("is_synthetic = true", name="ck_anomaly_is_synthetic"),
        sa.CheckConstraint("affected_population >= 0", name="ck_anomaly_population"),
        sa.CheckConstraint("cohort_depth >= 1", name="ck_anomaly_cohort_depth"),
    )
    op.create_index("ix_anomaly_run_rank", "incident_anomalies", ["analysis_run_id", "rank"])
    op.create_index("ix_anomaly_incident", "incident_anomalies", ["incident_id"])

    # ------------------------------------------------------------ incident_evidence
    op.create_table(
        "incident_evidence",
        sa.Column("evidence_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("anomaly_id", sa.BigInteger(), nullable=True),
        sa.Column("evidence_type", sa.Text(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("cohort_key", sa.Text(), nullable=False),
        sa.Column("cohort_definition", postgresql.JSONB(), nullable=False),
        sa.Column("gateway_id", sa.Text(), nullable=True),
        sa.Column("segment", postgresql.JSONB(), nullable=True),
        sa.Column("baseline_value", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("current_value", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("delta", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("relative_delta", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("significance_sigma", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("control_group", postgresql.JSONB(), nullable=True),
        sa.Column("source_layer", sa.Text(), nullable=False),
        sa.Column("evidence_source", sa.Text(), nullable=False),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_incident_evidence"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["incident_analysis_runs.analysis_run_id"],
            name="fk_evidence_analysis_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_evidence_incident",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["anomaly_id"],
            ["incident_anomalies.anomaly_id"],
            name="fk_evidence_anomaly",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("is_synthetic = true", name="ck_evidence_is_synthetic"),
        sa.CheckConstraint(f"evidence_type IN ({_EVIDENCE_TYPES})", name="ck_evidence_type"),
        sa.CheckConstraint(f"source_layer IN ({_SOURCE_LAYERS})", name="ck_evidence_source_layer"),
    )
    op.create_index("ix_evidence_run", "incident_evidence", ["analysis_run_id"])
    op.create_index("ix_evidence_anomaly", "incident_evidence", ["anomaly_id"])
    op.create_index("ix_evidence_incident", "incident_evidence", ["incident_id"])

    # ---------------------------------------------------------- incident_hypotheses
    op.create_table(
        "incident_hypotheses",
        sa.Column("hypothesis_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("hypothesis_type", sa.Text(), nullable=False),
        sa.Column("hypothesis_statement", sa.Text(), nullable=False),
        sa.Column("subject_dimension", sa.Text(), nullable=True),
        sa.Column("subject_value", sa.Text(), nullable=True),
        sa.Column("score", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "supporting_evidence_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False
        ),
        sa.Column(
            "contradicting_evidence_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False
        ),
        sa.Column("score_components", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("hypothesis_id", name="pk_incident_hypotheses"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["incident_analysis_runs.analysis_run_id"],
            name="fk_hypothesis_analysis_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.incident_id"],
            name="fk_hypothesis_incident",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("is_synthetic = true", name="ck_hypothesis_is_synthetic"),
        sa.CheckConstraint(f"hypothesis_type IN ({_HYPOTHESIS_TYPES})", name="ck_hypothesis_type"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_hypothesis_confidence"),
        sa.CheckConstraint("rank >= 1", name="ck_hypothesis_rank"),
    )
    op.create_index(
        "ix_hypothesis_run_rank", "incident_hypotheses", ["analysis_run_id", "rank"]
    )

    # --------------------------------------------------------- incident_rca_results
    op.create_table(
        "incident_rca_results",
        sa.Column("rca_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("predicted_root_cause", sa.Text(), nullable=True),
        sa.Column("predicted_hypothesis_type", sa.Text(), nullable=True),
        sa.Column("predicted_gateway_id", sa.Text(), nullable=True),
        sa.Column("predicted_segment", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("affected_population", postgresql.JSONB(), nullable=False),
        sa.Column("control_population", postgresql.JSONB(), nullable=False),
        sa.Column("incident_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("incident_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "supporting_evidence_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False
        ),
        sa.Column(
            "contradicting_evidence_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False
        ),
        sa.Column("alternatives_considered", postgresql.JSONB(), nullable=False),
        sa.Column("rca_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("rca_id", name="pk_incident_rca_results"),
        sa.UniqueConstraint("analysis_run_id", name="uq_rca_analysis_run"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["incident_analysis_runs.analysis_run_id"],
            name="fk_rca_analysis_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.incident_id"], name="fk_rca_incident", ondelete="CASCADE"
        ),
        sa.CheckConstraint("is_synthetic = true", name="ck_rca_is_synthetic"),
        sa.CheckConstraint(f"verdict IN ({_RCA_VERDICTS})", name="ck_rca_verdict"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_rca_confidence"),
        sa.CheckConstraint(
            "(verdict = 'INSUFFICIENT_EVIDENCE') = (predicted_root_cause IS NULL)",
            name="ck_rca_verdict_cause_coherent",
        ),
    )
    op.create_index("ix_rca_incident", "incident_rca_results", ["incident_id"])


def downgrade() -> None:
    # Reverse creation order so foreign keys unwind cleanly.
    op.drop_table("incident_rca_results")
    op.drop_table("incident_hypotheses")
    op.drop_table("incident_evidence")
    op.drop_table("incident_anomalies")
    op.drop_table("incident_analysis_runs")
    op.drop_table("simulated_incident_outcomes")
    op.drop_table("incident_simulation_runs")
    op.drop_table("incident_ground_truth")
    op.drop_table("incidents")
