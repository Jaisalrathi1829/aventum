"""
Day 5 persisted verification state.

One table. A verification is a durable, independently-computed judgement about one
executed action, and it is written once per (action, model version) — re-verifying the
same action under the same verifier converges on the existing row rather than minting a
second, differently-worded opinion that a UI could then choose between.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aventum_action.models import Base

# Python `None` must reach PostgreSQL as SQL NULL, not as a JSON `null` scalar --
# the Day 4A lesson that broke a CHECK constraint. Same binding here.
_Json = JSONB(none_as_null=True)


class Verification(Base):
    """An independent deterministic judgement on one executed action."""

    __tablename__ = "verifications"

    verification_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    action_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    incident_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    recommendation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    simulation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    ineligible_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- what was measured, kept strictly apart -----------------------------------
    # The baseline the action started from.
    baseline_failure_rate: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    baseline_success_rate: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    baseline_gmv_at_risk: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)
    # What the SIMULATION projected would happen.
    projected_success_delta: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    projected_gmv_retained: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)
    # What the ADAPTER actually modelled. Never merged with the projection.
    actual_failure_rate: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    actual_success_rate: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    actual_gmv_at_risk: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)

    # ---- what verification concluded -----------------------------------------------
    measured_success_delta: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    measured_failure_rate_improvement: Mapped[object | None] = mapped_column(
        Numeric(9, 6), nullable=True
    )
    actual_gmv_recovered: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)
    variance_vs_projection: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    attainment_ratio: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    transactions_moved: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    population: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    integrity_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    integrity_checks: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    cohort_definition: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    measurement_window: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    metric_definitions: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    reasons: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    limitations: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    verification_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[str] = mapped_column(Text, nullable=False)
    # Structural honesty, the Day 4A precedent: the database itself refuses to record a
    # verification of anything other than a simulated execution.
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    verified_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(["action_id"], ["actions.action_id"], name="fk_verification_action"),
        ForeignKeyConstraint(
            ["incident_id"], ["incidents.incident_id"], name="fk_verification_incident"
        ),
        # Re-verifying an action under the same verifier converges rather than forking.
        UniqueConstraint("action_id", "model_version", name="uq_verification_identity"),
        CheckConstraint("is_simulated = true", name="ck_verification_is_simulated"),
        CheckConstraint(
            "status IN ('COMPLETE', 'INELIGIBLE')", name="ck_verification_status"
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN "
            "('RECOVERY_EFFECTIVE', 'PARTIALLY_EFFECTIVE', 'RECOVERY_NOT_VERIFIED')",
            name="ck_verification_outcome",
        ),
        # A completed verification must reach a verdict; an ineligible one must not
        # pretend to. The pairing is enforced here rather than trusted to callers.
        CheckConstraint(
            "(status = 'COMPLETE') = (outcome IS NOT NULL)",
            name="ck_verification_outcome_coherent",
        ),
        CheckConstraint(
            "(status = 'INELIGIBLE') = (ineligible_reason IS NOT NULL)",
            name="ck_verification_ineligible_coherent",
        ),
        Index("ix_verification_action", "action_id"),
        Index("ix_verification_incident", "incident_id"),
    )
