"""
SQLAlchemy models for the Day 2B synthetic infrastructure layer.

Naming: every table carries a `synthetic_` prefix. This is deliberate and is the
strongest available provenance signal -- a future RCA/LLM tool cannot read
`synthetic_infrastructure_assignments` and mistake it for observed payment history the
way it might misread a bare `gateways`. It refines (does not contradict) the
docs/DATABASE_DESIGN.md proposal, which already required
`is_synthetic NOT NULL DEFAULT true CHECK (is_synthetic = true)` on these tables.

These models share `aventum_ingest.models.Base` so a single Alembic metadata target
covers both packages; Day 2B never modifies a Day 2A table.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aventum_ingest.models import Base

# Health vocabulary. UNAVAILABLE is defined now so Day 2C can use it without a
# migration; the Day 2B baseline emits only HEALTHY.
HEALTH_STATES = ("HEALTHY", "DEGRADED", "UNAVAILABLE")

# Latency regimes, coherent with the response taxonomy (see outcome_model.py).
LATENCY_REGIMES = ("NORMAL", "ELEVATED", "TIMEOUT")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


class SyntheticGenerationRun(Base):
    """
    One row per synthetic generation attempt -- the reproducibility/audit anchor.

    Mirrors `ingestion_runs` so the synthetic layer is auditable in the same way the
    canonical load is: which canonical ingestion it was built against, with which seed,
    config, and model version, and what fingerprint the result hashed to.
    """

    __tablename__ = "synthetic_generation_runs"

    generation_run_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    # The canonical load this synthetic population belongs to. A mismatch against the
    # current `transactions.ingestion_run_id` means this generation is STALE.
    source_ingestion_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.ingestion_run_id", name="fk_synth_run_ingestion_run"),
        nullable=False,
    )
    generation_seed: Mapped[str] = mapped_column(Text, nullable=False)
    generation_config_version: Mapped[str] = mapped_column(Text, nullable=False)
    synthetic_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    routing_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    calibration_reference_name: Mapped[str] = mapped_column(Text, nullable=False)
    calibration_reference_version: Mapped[str] = mapped_column(Text, nullable=False)

    started_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[object | None] = mapped_column(Numeric(12, 3), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    rows_generated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    generation_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_failure_rate: Mapped[object | None] = mapped_column(Numeric(8, 6), nullable=True)
    model_parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    distribution_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    __table_args__ = (
        CheckConstraint("is_synthetic = true", name="ck_synth_run_is_synthetic"),
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SUPERSEDED')",
            name="ck_synth_run_status",
        ),
        CheckConstraint("rows_generated >= 0", name="ck_synth_run_rows_generated"),
        Index("ix_synth_run_source_ingestion", "source_ingestion_run_id"),
        Index("ix_synth_run_status", "status"),
    )


class SyntheticGateway(Base):
    """
    The synthetic gateway universe.

    These are AVENTUM MODEL ENTITIES. They are not real Razorpay gateways and do not
    correspond to any real payment processor.
    """

    __tablename__ = "synthetic_gateways"

    gateway_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Which calibration-reference rail informed this gateway's RELATIVE profile.
    # Provenance only -- no reference row is imported.
    calibration_source_rail: Mapped[str | None] = mapped_column(Text, nullable=True)
    calibration_reference_name: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("is_synthetic = true", name="ck_synth_gateway_is_synthetic"),
        CheckConstraint("length(gateway_id) > 0", name="ck_synth_gateway_id_nonempty"),
    )


class SyntheticGatewayProfile(Base):
    """
    Versioned behavioural parameters per gateway -- the data-driven model config.

    Gateway behaviour lives HERE rather than in application code, so Day 2C can add a
    new profile version (or an incident-time override) without touching the generator.
    """

    __tablename__ = "synthetic_gateway_profiles"

    profile_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gateway_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_synth_profile_gateway"),
        nullable=False,
    )
    profile_version: Mapped[str] = mapped_column(Text, nullable=False)

    baseline_traffic_weight: Mapped[object] = mapped_column(Numeric(8, 6), nullable=False)
    # Unitless multiplier against the fleet-average failure rate (see calibration.py).
    relative_failure_multiplier: Mapped[object] = mapped_column(Numeric(8, 6), nullable=False)
    # Absolute probability, anchored to the OBSERVED canonical failure rate.
    baseline_failure_probability: Mapped[object] = mapped_column(Numeric(8, 6), nullable=False)
    latency_multiplier: Mapped[object] = mapped_column(Numeric(8, 6), nullable=False)
    # {response_code: share_of_failures}
    failure_response_mix: Mapped[dict] = mapped_column(JSONB, nullable=False)

    calibration_source_rail: Mapped[str | None] = mapped_column(Text, nullable=True)
    calibration_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        UniqueConstraint("gateway_id", "profile_version", name="uq_synth_profile_gateway_version"),
        CheckConstraint("is_synthetic = true", name="ck_synth_profile_is_synthetic"),
        CheckConstraint(
            "baseline_traffic_weight > 0 AND baseline_traffic_weight <= 1",
            name="ck_synth_profile_traffic_weight",
        ),
        CheckConstraint(
            "baseline_failure_probability >= 0 AND baseline_failure_probability < 1",
            name="ck_synth_profile_failure_probability",
        ),
        CheckConstraint("latency_multiplier > 0", name="ck_synth_profile_latency_multiplier"),
        Index("ix_synth_profile_version", "profile_version"),
    )


class SyntheticRoutingPolicy(Base):
    """
    Versioned routing-policy abstraction.

    This is a SYNTHETIC BASELINE ASSIGNMENT POLICY. It is explicitly not adaptive, not
    intelligent, and not a representation of any real processor's routing algorithm.
    """

    __tablename__ = "synthetic_routing_policies"

    policy_version: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # e.g. 'deterministic_hash_weighted_status_conditioned'
    selection_method: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("is_synthetic = true", name="ck_synth_policy_is_synthetic"),
        CheckConstraint("length(policy_version) > 0", name="ck_synth_policy_version_nonempty"),
    )


class SyntheticRoutingPolicyGateway(Base):
    """
    Gateway eligibility and weight under one policy version.

    Keeping eligibility as data (rather than an `if` in the generator) is what lets a
    later component answer "why was this gateway eligible?" from the database.
    """

    __tablename__ = "synthetic_routing_policy_gateways"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    policy_version: Mapped[str] = mapped_column(
        Text,
        ForeignKey("synthetic_routing_policies.policy_version", name="fk_synth_pg_policy"),
        nullable=False,
    )
    gateway_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_synth_pg_gateway"),
        nullable=False,
    )
    traffic_weight: Mapped[object] = mapped_column(Numeric(8, 6), nullable=False)
    # NULL = eligible for all traffic. Day 2C may add scoped conditions without a
    # migration (e.g. {"payment_method": ["P2M"]}).
    eligibility_conditions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        UniqueConstraint("policy_version", "gateway_id", name="uq_synth_pg_policy_gateway"),
        CheckConstraint("is_synthetic = true", name="ck_synth_pg_is_synthetic"),
        CheckConstraint("traffic_weight >= 0", name="ck_synth_pg_traffic_weight"),
    )


class SyntheticGatewayHealthState(Base):
    """
    Gateway health over a validity window.

    Day 2B writes exactly one HEALTHY window per gateway spanning the canonical dataset.
    Day 2C injects DEGRADED/UNAVAILABLE windows here -- no schema change required, which
    is the point of modelling health as an interval rather than a column.

    Health is a MODEL STATE, not an observation. What Aventum would later *observe* is
    rolling failure/latency metrics; health is the underlying state those metrics are
    generated from, and which a future health assessor would try to infer.
    """

    __tablename__ = "synthetic_gateway_health_states"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gateway_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_synth_health_gateway"),
        nullable=False,
    )
    generation_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "synthetic_generation_runs.generation_run_id",
            name="fk_synth_health_generation_run",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    health_state: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    # Multipliers a non-HEALTHY window applies on top of the baseline profile. Day 2B
    # always writes 1.0; Day 2C raises them to express a degradation.
    failure_multiplier: Mapped[object] = mapped_column(
        Numeric(8, 4), nullable=False, server_default="1.0"
    )
    latency_multiplier: Mapped[object] = mapped_column(
        Numeric(8, 4), nullable=False, server_default="1.0"
    )
    timeout_multiplier: Mapped[object] = mapped_column(
        Numeric(8, 4), nullable=False, server_default="1.0"
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("is_synthetic = true", name="ck_synth_health_is_synthetic"),
        CheckConstraint(
            f"health_state IN ({_sql_list(HEALTH_STATES)})", name="ck_synth_health_state"
        ),
        CheckConstraint("valid_to > valid_from", name="ck_synth_health_window"),
        CheckConstraint("failure_multiplier > 0", name="ck_synth_health_failure_multiplier"),
        CheckConstraint("latency_multiplier > 0", name="ck_synth_health_latency_multiplier"),
        CheckConstraint("timeout_multiplier > 0", name="ck_synth_health_timeout_multiplier"),
        Index("ix_synth_health_gateway_window", "gateway_id", "valid_from", "valid_to"),
        Index("ix_synth_health_generation_run", "generation_run_id"),
    )


class SyntheticInfrastructureAssignment(Base):
    """
    The per-transaction synthetic infrastructure record -- Day 2B's primary output.

    One row per canonical transaction per generation run. Holds both the ROUTING
    DECISION CONTEXT (why this gateway, under which policy) and the GENERATED SIGNALS
    (latency, response code, health at the time), so a later RCA/simulation component
    has everything it needs without re-deriving the model.

    Referential design:
      - `transaction_id` FK ON DELETE CASCADE. Day 2A's promotion deletes and re-inserts
        canonical rows, so cascading keeps Day 2A's idempotency guarantee intact while
        making a re-ingestion unambiguously wipe (rather than silently orphan) the
        synthetic population. See the staleness policy in docs/DAY2B_INFRASTRUCTURE_REPORT.md.
      - `source_ingestion_run_id` is stored redundantly so staleness is detectable by
        comparison, not only by absence.
    """

    __tablename__ = "synthetic_infrastructure_assignments"

    assignment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # --- Linkage to observed canonical data (read-only side) ---
    transaction_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "transactions.transaction_id",
            name="fk_synth_assignment_transaction",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    source_ingestion_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.ingestion_run_id", name="fk_synth_assignment_ingestion_run"),
        nullable=False,
    )
    generation_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "synthetic_generation_runs.generation_run_id",
            name="fk_synth_assignment_generation_run",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    # --- Routing decision context ---
    routing_policy_version: Mapped[str] = mapped_column(
        Text,
        ForeignKey("synthetic_routing_policies.policy_version", name="fk_synth_assignment_policy"),
        nullable=False,
    )
    eligible_gateways: Mapped[dict] = mapped_column(JSONB, nullable=False)
    selected_gateway_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_synth_assignment_gateway"),
        nullable=False,
    )
    selection_method: Mapped[str] = mapped_column(Text, nullable=False)
    selection_seed: Mapped[str] = mapped_column(Text, nullable=False)
    gateway_profile_version: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Generated infrastructure signals ---
    gateway_health_state: Mapped[str] = mapped_column(Text, nullable=False)
    latency_regime: Mapped[str] = mapped_column(Text, nullable=False)
    gateway_latency_ms: Mapped[object] = mapped_column(Numeric(10, 2), nullable=False)
    gateway_response_code: Mapped[str] = mapped_column(Text, nullable=False)
    response_attribution: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Model transparency ---
    # The forward model's belief for this (gateway, context, health). NOT the cause of
    # the observed status -- see docs/DAY2B_TRUTH_MODEL.md. Retained so Day 2C's
    # counterfactual simulator has the baseline probability it needs.
    modeled_failure_probability: Mapped[object] = mapped_column(Numeric(8, 6), nullable=False)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        # One synthetic assignment per transaction per generation run.
        UniqueConstraint(
            "transaction_id", "generation_run_id", name="uq_synth_assignment_txn_run"
        ),
        CheckConstraint("is_synthetic = true", name="ck_synth_assignment_is_synthetic"),
        CheckConstraint(
            f"gateway_health_state IN ({_sql_list(HEALTH_STATES)})",
            name="ck_synth_assignment_health_state",
        ),
        CheckConstraint(
            f"latency_regime IN ({_sql_list(LATENCY_REGIMES)})",
            name="ck_synth_assignment_latency_regime",
        ),
        CheckConstraint("gateway_latency_ms > 0", name="ck_synth_assignment_latency_positive"),
        CheckConstraint(
            "modeled_failure_probability >= 0 AND modeled_failure_probability < 1",
            name="ck_synth_assignment_failure_probability",
        ),
        CheckConstraint(
            "gateway_response_code IN ('APPROVED', 'INSUFFICIENT_FUNDS', 'ISSUER_DECLINED', "
            "'PROCESSING_ERROR', 'DO_NOT_HONOR', 'TIMEOUT')",
            name="ck_synth_assignment_response_code",
        ),
        CheckConstraint(
            "response_attribution IN ('approved', 'issuer_side', 'infrastructure_side')",
            name="ck_synth_assignment_response_attribution",
        ),
        # --- Internal coherence, enforced by the database (Day 2B §17) ---
        # A TIMEOUT response may only occur in the TIMEOUT latency regime, and vice
        # versa. This is what makes "SUCCESS + TIMEOUT" structurally impossible once
        # combined with the APPROVED rule below.
        CheckConstraint(
            "(gateway_response_code = 'TIMEOUT') = (latency_regime = 'TIMEOUT')",
            name="ck_synth_assignment_timeout_coherence",
        ),
        # An APPROVED response must be attributed 'approved', and a declined response
        # must not be. Deliberately NOT forcing APPROVED into the NORMAL regime: a
        # slow-but-successful payment is realistic, and forbidding it would make latency
        # a perfect predictor of outcome. The genuinely impossible case -- APPROVED with
        # a TIMEOUT latency -- is already excluded by the timeout coherence constraint.
        CheckConstraint(
            "(gateway_response_code = 'APPROVED' AND response_attribution = 'approved') "
            "OR (gateway_response_code <> 'APPROVED' AND response_attribution <> 'approved')",
            name="ck_synth_assignment_approved_coherence",
        ),
        Index("ix_synth_assignment_transaction", "transaction_id"),
        Index("ix_synth_assignment_generation_run", "generation_run_id"),
        Index("ix_synth_assignment_source_ingestion", "source_ingestion_run_id"),
        Index("ix_synth_assignment_gateway", "selected_gateway_id"),
        Index("ix_synth_assignment_policy", "routing_policy_version"),
    )
