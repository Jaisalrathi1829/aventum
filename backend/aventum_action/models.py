"""
SQLAlchemy models for the Day 4A action layer.

Three structural guarantees live in the constraints below rather than in application
code, because application code can be bypassed by a bug and a constraint cannot:

  * `uq_action_idempotency` -- UNIQUE on `actions.idempotency_key`. Concurrent execution
    requests for the same (recommendation, approval, adapter) are serialised by
    PostgreSQL; exactly one row can exist, so the adapter runs exactly once.
  * `ck_action_is_simulated` -- `CHECK (is_simulated = true)`. The database refuses to
    record a Day 4 execution as real. The honesty boundary is a constraint.
  * approvals are APPEND-ONLY -- a decision is a new row, never an UPDATE of an old one,
    so "who approved what, when, and against which content" stays reconstructable.

`expected_outcome` and `actual_simulated_outcome` are separate columns and must never be
merged: the gap between them is exactly what Day 5 exists to measure.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aventum_ingest.models import Base

# Side-effect imports: register the prior-layer tables on the shared metadata so this
# module's foreign keys resolve. Day 4 never writes to any of them.
from aventum_counterfactual import models as _cf_models  # noqa: F401
from aventum_incident import models as _incident_models  # noqa: F401
from aventum_synth import models as _synth_models  # noqa: F401

# JSONB columns must map a Python None to SQL NULL, not to a JSON 'null' literal.
# Without none_as_null, `policy_reason_codes IS NOT NULL` evaluates TRUE for a JSON
# null, and the coherence CHECK would reject a legitimately PERMITTED recommendation --
# which is exactly how this was found: the constraint refused the first real run.
_Json = JSONB(none_as_null=True)

RECOMMENDATION_STATUSES = (
    "ABANDONED",
    "APPROVED",
    "AWAITING_APPROVAL",
    "BLOCKED",
    "DRAFT",
    "EXECUTED",
    "EXPIRED",
    "PERMITTED",
    "REJECTED",
    "SUPERSEDED",
)
APPROVAL_STATUSES = ("APPROVED", "EXPIRED", "PENDING", "REJECTED")
ACTION_STATUSES = ("EXECUTED", "FAILED", "PENDING", "REJECTED", "ROLLED_BACK")
POLICY_RESULTS = ("BLOCKED", "PERMITTED")
ACTION_TYPES = ("NO_ACTION", "REROUTE")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


class Recommendation(Base):
    """
    A bounded recovery proposal, tied to exactly one persisted simulation.

    `simulation_id` is NOT NULL by design: a recommendation with no simulation behind it
    would have nowhere for its numbers to come from except its caller, which is the one
    thing this layer must make impossible.
    """

    __tablename__ = "recommendations"

    recommendation_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.incident_id", name="fk_rec_incident"), nullable=False
    )
    analysis_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incident_analysis_runs.analysis_run_id", name="fk_rec_analysis_run"),
        nullable=False,
    )
    simulation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("counterfactual_simulations.simulation_id", name="fk_rec_simulation"),
        nullable=False,
    )
    # NULL for every Day 4A row. Day 4B attaches an agent run here without a migration.
    agent_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agent_runs.agent_run_id", name="fk_rec_agent_run"), nullable=True
    )

    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_gateway_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_rec_source_gateway"),
        nullable=True,
    )
    target_gateway_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_rec_target_gateway"),
        nullable=True,
    )
    traffic_percentage: Mapped[object] = mapped_column(
        Numeric(5, 2), nullable=False, server_default="0"
    )

    # --- copied from the simulation, server-side. Never accepted from a caller. ---
    expected_success_delta: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    expected_gmv_retained: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)
    expected_latency_delta_ms: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    risk_score: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    risk_components: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    # --- copied from the RCA row: the Day 3 P1-2 quartet, kept separate forever ---
    confidence: Mapped[object | None] = mapped_column(Numeric(9, 4), nullable=True)
    evidence_strength: Mapped[object | None] = mapped_column(Numeric(9, 4), nullable=True)
    significance_sigma: Mapped[object | None] = mapped_column(Numeric(12, 4), nullable=True)
    severity: Mapped[str | None] = mapped_column(Text, nullable=True)

    supporting_evidence_ids: Mapped[list | None] = mapped_column(ARRAY(BigInteger), nullable=True)
    alternatives_considered: Mapped[list | None] = mapped_column(_Json, nullable=True)

    # The ONLY field an eventual agent layer may author. NULL in Day 4A: the
    # deterministic spine produces recommendations with no narrative at all, which is
    # exactly what proves the spine does not depend on one.
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    policy_validation_result: Mapped[str] = mapped_column(Text, nullable=False)
    policy_reason_codes: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    constraints: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    recommendation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "incident_id", "simulation_id", "policy_version", name="uq_recommendation_identity"
        ),
        CheckConstraint(f"status IN ({_sql_list(RECOMMENDATION_STATUSES)})", name="ck_rec_status"),
        CheckConstraint(f"action_type IN ({_sql_list(ACTION_TYPES)})", name="ck_rec_action_type"),
        CheckConstraint(
            f"policy_validation_result IN ({_sql_list(POLICY_RESULTS)})",
            name="ck_rec_policy_result",
        ),
        CheckConstraint(
            "traffic_percentage >= 0 AND traffic_percentage <= 100", name="ck_rec_traffic_range"
        ),
        CheckConstraint(
            "(policy_validation_result = 'BLOCKED') = (policy_reason_codes IS NOT NULL)",
            name="ck_rec_reason_codes_coherent",
        ),
        CheckConstraint(
            "(action_type = 'NO_ACTION' AND target_gateway_id IS NULL "
            " AND traffic_percentage = 0) "
            "OR (action_type = 'REROUTE' AND target_gateway_id IS NOT NULL)",
            name="ck_rec_action_shape",
        ),
        Index("ix_rec_incident_status", "incident_id", "status"),
    )


class Approval(Base):
    """
    A human decision. Append-only: a re-validation cycle adds a row, never overwrites one.

    The partial unique index (created in migration 0006) allows at most one PENDING
    approval per recommendation, so an approval cannot be raced or duplicated.
    """

    __tablename__ = "approvals"

    approval_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("recommendations.recommendation_id", name="fk_approval_recommendation"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    # Required when decided. A decision with no human attached is not an approval.
    approver_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Binds to the recommendation content shown. An edited recommendation no longer
    # matches, so an approval cannot be transferred to a different proposal.
    approval_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_sql_list(APPROVAL_STATUSES)})", name="ck_approval_status"),
        CheckConstraint(
            "(status IN ('APPROVED', 'REJECTED')) "
            "= (approver_identity IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_approval_decision_coherent",
        ),
        Index("ix_appr_recommendation", "recommendation_id", "status"),
    )


class Action(Base):
    """
    One simulated execution attempt.

    `idempotency_key` is UNIQUE: this is the structural defense against duplicate
    execution, and the reason the concurrency guarantee comes from PostgreSQL rather
    than from careful application sequencing.
    """

    __tablename__ = "actions"

    action_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("recommendations.recommendation_id", name="fk_action_recommendation"),
        nullable=False,
    )
    approval_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approvals.approval_id", name="fk_action_approval"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revalidation_result: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    # --- Day 5 verification inputs ---
    pre_action_metrics: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    # What the simulation projected. Never merged with what the adapter modelled.
    expected_outcome: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    actual_simulated_outcome: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    cohort_definition: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    measurement_window: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    execution_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reference_simulation_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rollback_of_action_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("actions.action_id", name="fk_action_rollback_of"), nullable=True
    )
    rollback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_action_idempotency"),
        CheckConstraint("is_simulated = true", name="ck_action_is_simulated"),
        CheckConstraint(f"status IN ({_sql_list(ACTION_STATUSES)})", name="ck_action_status"),
        CheckConstraint(
            "(status = 'REJECTED') = (rejection_reason IS NOT NULL)",
            name="ck_action_rejection_coherent",
        ),
        # ROLLED_BACK keeps its executed_at -- it really was executed, then reverted.
        CheckConstraint(
            "(status IN ('EXECUTED', 'ROLLED_BACK')) = (executed_at IS NOT NULL)",
            name="ck_action_executed_coherent",
        ),
        Index("ix_action_recommendation", "recommendation_id"),
    )


class AuditEvent(Base):
    """
    The append-only spine. No module exposes an UPDATE or DELETE path for this table.

    `payload` carries a structured summary only. Chain-of-thought is never stored -- and
    in Day 4B, with `think:false`, none will be produced to store.
    """

    __tablename__ = "audit_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incidents.incident_id", name="fk_audit_incident"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    # SYSTEM | AGENT | HUMAN:<identity>
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    # {table, id} pointers rather than copies, so the audit trail never drifts from the
    # rows it describes.
    input_ref: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    output_ref: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    payload: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    model_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("length(actor) > 0", name="ck_audit_actor_nonempty"),
        CheckConstraint("length(event_type) > 0", name="ck_audit_event_type_nonempty"),
        Index("ix_audit_incident_time", "incident_id", "occurred_at"),
        Index("ix_audit_type", "event_type"),
    )
