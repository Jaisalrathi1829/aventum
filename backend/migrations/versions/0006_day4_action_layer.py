"""Day 4A action layer: counterfactual simulation, recommendation, approval, simulated execution, audit.

Adds seven tables per docs/DAY4_DATABASE_CONTRACT.md §2. Additive only: no ALTER, no
DROP, and no data rewrite touches any Day 1, Day 2, or Day 3 table. `transactions`, the
`synthetic_*` baseline, and the nine Day 3 incident tables are read-only from Day 4's
perspective and this migration does not modify a single one of them.

PROVENANCE IS MACHINE-ENFORCED, following the Day 2B/Day 3 precedent
--------------------------------------------------------------------
`counterfactual_simulations.is_simulated` and `actions.is_simulated` both carry
`NOT NULL DEFAULT true CHECK (= true)`. Day 4 execution is SIMULATED ONLY, and the
database itself refuses to record an action as real. That is the honesty boundary
expressed as a constraint rather than a comment.

THREE STRUCTURAL SAFETY PROPERTIES LIVE HERE, NOT IN APPLICATION CODE
--------------------------------------------------------------------
1. `uq_action_idempotency` — UNIQUE on `actions.idempotency_key`. Two concurrent
   execution requests for the same (recommendation, approval, adapter) cannot both
   insert; PostgreSQL serialises them and the loser is deflected to the winner's
   result. Duplicate execution is impossible by construction, not by careful coding.

2. `uq_approval_one_pending` — a PARTIAL unique index over
   `(recommendation_id) WHERE status = 'PENDING'`. At most one approval can be
   outstanding for a recommendation at any moment, so an approval cannot be raced.

3. `uq_simulation_identity` — UNIQUE on
   `(incident_id, candidate_key, input_fingerprint)`. Re-simulating identical inputs
   converges on the existing row instead of minting a second, divergent projection
   that a recommendation could then cite selectively.

`capacity_utilization` is created NULLABLE and is documented to stay NULL for the whole
of Day 4 (contract §6): no capacity telemetry exists anywhere in Day 2B, so the column
exists to hold a value a future real-telemetry substitution could supply, and inventing
one now would be precisely the fabricated production figure the project forbids.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_SIM_STATUSES = "'SIMULATION_INVALID', 'VALID'"
_ACTION_TYPES = "'NO_ACTION', 'REROUTE'"
_AGENT_RUN_STATUSES = (
    "'AGENT_UNAVAILABLE', 'BUDGET_EXCEEDED', 'FAILED', 'RUNNING', 'SUCCEEDED'"
)
_TOOL_OUTCOMES = (
    "'INSUFFICIENT_EVIDENCE', 'INTERNAL_ERROR', 'INVALID_REQUEST', 'NO_DATA', "
    "'SAFETY_BLOCK', 'SUCCESS', 'TIMEOUT'"
)
_RECOMMENDATION_STATUSES = (
    "'ABANDONED', 'APPROVED', 'AWAITING_APPROVAL', 'BLOCKED', 'DRAFT', 'EXECUTED', "
    "'EXPIRED', 'PERMITTED', 'REJECTED', 'SUPERSEDED'"
)
_POLICY_RESULTS = "'BLOCKED', 'PERMITTED'"
_APPROVAL_STATUSES = "'APPROVED', 'EXPIRED', 'PENDING', 'REJECTED'"
_ACTION_STATUSES = "'EXECUTED', 'FAILED', 'PENDING', 'REJECTED', 'ROLLED_BACK'"


def upgrade() -> None:
    # ------------------------------------------------------------------ agent_runs
    # Created in Day 4A although no agent exists yet: the simulation table carries a
    # nullable FK to it, and Day 4B must be able to attach a run without a migration.
    # Every Day 4A row leaves `agent_run_id` NULL, which is exactly the
    # "produced deterministically, without narrative" case the contract describes.
    op.create_table(
        "agent_runs",
        sa.Column("agent_run_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("model_options", postgresql.JSONB(), nullable=True),
        sa.Column("turns_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("tool_calls_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("simulations_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("context_tokens_max", sa.Integer(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("agent_run_id", name="pk_agent_runs"),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.incident_id"], name="fk_agent_run_incident"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["incident_analysis_runs.analysis_run_id"],
            name="fk_agent_run_analysis",
        ),
        sa.CheckConstraint(f"status IN ({_AGENT_RUN_STATUSES})", name="ck_agent_run_status"),
        sa.CheckConstraint("turns_used >= 0", name="ck_agent_run_turns"),
        sa.CheckConstraint("tool_calls_used >= 0", name="ck_agent_run_tool_calls"),
        sa.CheckConstraint("simulations_used >= 0", name="ck_agent_run_simulations"),
    )

    # ------------------------------------------------------------- agent_tool_calls
    op.create_table(
        "agent_tool_calls",
        sa.Column("tool_call_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=True),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("tool_call_id", name="pk_agent_tool_calls"),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.agent_run_id"],
            name="fk_tool_call_agent_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_tool_call_run_sequence"),
        sa.CheckConstraint(f"outcome IN ({_TOOL_OUTCOMES})", name="ck_tool_call_outcome"),
        sa.CheckConstraint("sequence >= 0", name="ck_tool_call_sequence"),
        sa.CheckConstraint("attempt >= 1", name="ck_tool_call_attempt"),
    )
    op.create_index("ix_toolcall_run", "agent_tool_calls", ["agent_run_id", "sequence"])

    # ------------------------------------------------- counterfactual_simulations
    op.create_table(
        "counterfactual_simulations",
        sa.Column("simulation_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=True),
        sa.Column("candidate_key", sa.Text(), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("source_gateway_id", sa.Text(), nullable=True),
        sa.Column("target_gateway_id", sa.Text(), nullable=True),
        sa.Column(
            "traffic_percentage",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("invalid_reason", sa.Text(), nullable=True),
        sa.Column(
            "affected_population", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("rerouted_population", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("current_distribution", postgresql.JSONB(), nullable=True),
        sa.Column("projected_distribution", postgresql.JSONB(), nullable=True),
        sa.Column("baseline_success_rate", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("projected_success_rate", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("expected_success_delta", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("projected_failure_count", sa.Integer(), nullable=True),
        sa.Column("projected_gmv_total", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("projected_gmv_retained", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("projected_gmv_at_risk", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("projected_latency_p50", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("projected_latency_p95", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("latency_delta_ms", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("concentration_after", sa.Numeric(precision=6, scale=4), nullable=True),
        # Contract §6: stays NULL for all of Day 4. No capacity telemetry exists.
        sa.Column("capacity_utilization", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("eligibility_result", postgresql.JSONB(), nullable=True),
        sa.Column("risk_score", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("risk_components", postgresql.JSONB(), nullable=True),
        sa.Column("held_constant", postgresql.JSONB(), nullable=True),
        sa.Column("changed_variables", postgresql.JSONB(), nullable=True),
        sa.Column("assumptions", postgresql.JSONB(), nullable=True),
        sa.Column("limitations", postgresql.JSONB(), nullable=True),
        sa.Column("simulation_seed", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("simulation_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("elapsed_ms", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("simulation_id", name="pk_counterfactual_simulations"),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.incident_id"], name="fk_sim_incident"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["incident_analysis_runs.analysis_run_id"],
            name="fk_sim_analysis_run",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.agent_run_id"], name="fk_sim_agent_run"
        ),
        sa.ForeignKeyConstraint(
            ["source_gateway_id"], ["synthetic_gateways.gateway_id"], name="fk_sim_source_gateway"
        ),
        sa.ForeignKeyConstraint(
            ["target_gateway_id"], ["synthetic_gateways.gateway_id"], name="fk_sim_target_gateway"
        ),
        sa.UniqueConstraint(
            "incident_id", "candidate_key", "input_fingerprint", name="uq_simulation_identity"
        ),
        sa.CheckConstraint("is_simulated = true", name="ck_sim_is_simulated"),
        sa.CheckConstraint(f"status IN ({_SIM_STATUSES})", name="ck_sim_status"),
        sa.CheckConstraint(f"action_type IN ({_ACTION_TYPES})", name="ck_sim_action_type"),
        sa.CheckConstraint(
            "traffic_percentage >= 0 AND traffic_percentage <= 100", name="ck_sim_traffic_range"
        ),
        # An invalid simulation must say why; a valid one must not carry a reason.
        sa.CheckConstraint(
            "(status = 'SIMULATION_INVALID') = (invalid_reason IS NOT NULL)",
            name="ck_sim_invalid_reason_coherent",
        ),
        # NO_ACTION has no target and moves no traffic; REROUTE must name both ends.
        sa.CheckConstraint(
            "(action_type = 'NO_ACTION' AND target_gateway_id IS NULL "
            " AND traffic_percentage = 0) "
            "OR (action_type = 'REROUTE' AND target_gateway_id IS NOT NULL)",
            name="ck_sim_action_shape",
        ),
        # A reroute to the gateway traffic already sits on is not a counterfactual.
        sa.CheckConstraint(
            "source_gateway_id IS NULL OR target_gateway_id IS NULL "
            "OR source_gateway_id <> target_gateway_id",
            name="ck_sim_source_differs_from_target",
        ),
    )
    op.create_index(
        "ix_sim_incident", "counterfactual_simulations", ["incident_id", "candidate_key"]
    )
    op.create_index("ix_sim_fingerprint", "counterfactual_simulations", ["input_fingerprint"])

    # -------------------------------------------------------------- recommendations
    op.create_table(
        "recommendations",
        sa.Column("recommendation_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("analysis_run_id", sa.BigInteger(), nullable=False),
        sa.Column("simulation_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=True),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("source_gateway_id", sa.Text(), nullable=True),
        sa.Column("target_gateway_id", sa.Text(), nullable=True),
        sa.Column(
            "traffic_percentage",
            sa.Numeric(precision=5, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Every value below is COPIED from `simulation_id` server-side. The builder
        # accepts no numeric argument, so a caller cannot supply a different figure.
        sa.Column("expected_success_delta", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("expected_gmv_retained", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("expected_latency_delta_ms", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("risk_score", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("risk_components", postgresql.JSONB(), nullable=True),
        # Copied from the RCA row — the Day 3 P1-2 quartet, never collapsed to a scalar.
        sa.Column("confidence", sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column("evidence_strength", sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column("significance_sigma", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("severity", sa.Text(), nullable=True),
        sa.Column("supporting_evidence_ids", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("alternatives_considered", postgresql.JSONB(), nullable=True),
        # The ONLY field an eventual agent layer may author. Nullable because Day 4A
        # produces recommendations with no narrative at all.
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("policy_validation_result", sa.Text(), nullable=False),
        sa.Column("policy_reason_codes", postgresql.JSONB(), nullable=True),
        sa.Column("constraints", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recommendation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("recommendation_id", name="pk_recommendations"),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.incident_id"], name="fk_rec_incident"
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["incident_analysis_runs.analysis_run_id"],
            name="fk_rec_analysis_run",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id"],
            ["counterfactual_simulations.simulation_id"],
            name="fk_rec_simulation",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.agent_run_id"], name="fk_rec_agent_run"
        ),
        sa.ForeignKeyConstraint(
            ["source_gateway_id"], ["synthetic_gateways.gateway_id"], name="fk_rec_source_gateway"
        ),
        sa.ForeignKeyConstraint(
            ["target_gateway_id"], ["synthetic_gateways.gateway_id"], name="fk_rec_target_gateway"
        ),
        sa.UniqueConstraint(
            "incident_id", "simulation_id", "policy_version", name="uq_recommendation_identity"
        ),
        sa.CheckConstraint(f"status IN ({_RECOMMENDATION_STATUSES})", name="ck_rec_status"),
        sa.CheckConstraint(f"action_type IN ({_ACTION_TYPES})", name="ck_rec_action_type"),
        sa.CheckConstraint(
            f"policy_validation_result IN ({_POLICY_RESULTS})", name="ck_rec_policy_result"
        ),
        sa.CheckConstraint(
            "traffic_percentage >= 0 AND traffic_percentage <= 100", name="ck_rec_traffic_range"
        ),
        # A BLOCKED validation must carry its reason codes; PERMITTED must not claim any.
        sa.CheckConstraint(
            "(policy_validation_result = 'BLOCKED') = (policy_reason_codes IS NOT NULL)",
            name="ck_rec_reason_codes_coherent",
        ),
        sa.CheckConstraint(
            "(action_type = 'NO_ACTION' AND target_gateway_id IS NULL "
            " AND traffic_percentage = 0) "
            "OR (action_type = 'REROUTE' AND target_gateway_id IS NOT NULL)",
            name="ck_rec_action_shape",
        ),
    )
    op.create_index("ix_rec_incident_status", "recommendations", ["incident_id", "status"])
    op.create_index(
        "ix_rec_expires",
        "recommendations",
        ["expires_at"],
        postgresql_where=sa.text("status = 'AWAITING_APPROVAL'"),
    )

    # -------------------------------------------------------------------- approvals
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("recommendation_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approver_identity", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        # Binds the decision to the exact recommendation content that was shown.
        sa.Column("approval_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("approval_id", name="pk_approvals"),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["recommendations.recommendation_id"],
            name="fk_approval_recommendation",
        ),
        sa.CheckConstraint(f"status IN ({_APPROVAL_STATUSES})", name="ck_approval_status"),
        # A decided approval must name a human and a time. PENDING must not.
        sa.CheckConstraint(
            "(status IN ('APPROVED', 'REJECTED')) "
            "= (approver_identity IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_approval_decision_coherent",
        ),
    )
    op.create_index("ix_appr_recommendation", "approvals", ["recommendation_id", "status"])
    # At most one outstanding approval per recommendation — an approval cannot be raced.
    op.create_index(
        "uq_approval_one_pending",
        "approvals",
        ["recommendation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    # ---------------------------------------------------------------------- actions
    op.create_table(
        "actions",
        sa.Column("action_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("recommendation_id", sa.BigInteger(), nullable=False),
        sa.Column("approval_id", sa.BigInteger(), nullable=False),
        # SHA256(recommendation_id || approval_id || adapter_name). The UNIQUE below is
        # the structural defense against duplicate execution.
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("adapter_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("revalidation_result", postgresql.JSONB(), nullable=True),
        sa.Column("pre_action_metrics", postgresql.JSONB(), nullable=True),
        # Kept strictly separate from `expected_outcome`: the gap between them is what
        # Day 5 exists to measure, so merging them would destroy the verification.
        sa.Column("expected_outcome", postgresql.JSONB(), nullable=True),
        sa.Column("actual_simulated_outcome", postgresql.JSONB(), nullable=True),
        sa.Column("cohort_definition", postgresql.JSONB(), nullable=True),
        sa.Column("measurement_window", postgresql.JSONB(), nullable=True),
        sa.Column("execution_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("reference_simulation_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("rollback_of_action_id", sa.BigInteger(), nullable=True),
        sa.Column("rollback_reason", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_by", sa.Text(), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("action_id", name="pk_actions"),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["recommendations.recommendation_id"],
            name="fk_action_recommendation",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.approval_id"], name="fk_action_approval"
        ),
        sa.ForeignKeyConstraint(
            ["rollback_of_action_id"], ["actions.action_id"], name="fk_action_rollback_of"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_action_idempotency"),
        sa.CheckConstraint("is_simulated = true", name="ck_action_is_simulated"),
        sa.CheckConstraint(f"status IN ({_ACTION_STATUSES})", name="ck_action_status"),
        # A rejected action must say why; a successful one must not carry a reason.
        sa.CheckConstraint(
            "(status = 'REJECTED') = (rejection_reason IS NOT NULL)",
            name="ck_action_rejection_coherent",
        ),
        # ROLLED_BACK retains its executed_at: the action genuinely WAS executed before
        # it was reverted, and erasing that would break the "we acted, then reverted"
        # history rollback exists to preserve. PENDING and REJECTED never reach an
        # adapter, so they must carry no execution timestamp at all.
        sa.CheckConstraint(
            "(status IN ('EXECUTED', 'ROLLED_BACK')) = (executed_at IS NOT NULL)",
            name="ck_action_executed_coherent",
        ),
    )
    op.create_index("ix_action_recommendation", "actions", ["recommendation_id"])

    # ----------------------------------------------------------------- audit_events
    # Append-only: no UPDATE or DELETE path is exposed by any Day 4 module, and a
    # retry adds a row rather than rewriting one. History is never overwritten.
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("input_ref", postgresql.JSONB(), nullable=True),
        sa.Column("output_ref", postgresql.JSONB(), nullable=True),
        # Structured summary only. Chain-of-thought is never stored — and with
        # `think:false` in Day 4B, none will even be produced.
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Text(), nullable=True),
        sa.Column("tool_version", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["incident_id"], ["incidents.incident_id"], name="fk_audit_incident"
        ),
        sa.CheckConstraint("length(actor) > 0", name="ck_audit_actor_nonempty"),
        sa.CheckConstraint("length(event_type) > 0", name="ck_audit_event_type_nonempty"),
    )
    op.create_index("ix_audit_incident_time", "audit_events", ["incident_id", "occurred_at"])
    op.create_index("ix_audit_type", "audit_events", ["event_type"])


def downgrade() -> None:
    # Reverse creation order so foreign keys unwind cleanly.
    op.drop_index("ix_audit_type", table_name="audit_events")
    op.drop_index("ix_audit_incident_time", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_action_recommendation", table_name="actions")
    op.drop_table("actions")

    op.drop_index("uq_approval_one_pending", table_name="approvals")
    op.drop_index("ix_appr_recommendation", table_name="approvals")
    op.drop_table("approvals")

    op.drop_index("ix_rec_expires", table_name="recommendations")
    op.drop_index("ix_rec_incident_status", table_name="recommendations")
    op.drop_table("recommendations")

    op.drop_index("ix_sim_fingerprint", table_name="counterfactual_simulations")
    op.drop_index("ix_sim_incident", table_name="counterfactual_simulations")
    op.drop_table("counterfactual_simulations")

    op.drop_index("ix_toolcall_run", table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")

    op.drop_table("agent_runs")
