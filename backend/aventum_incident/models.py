"""
SQLAlchemy models for the Day 3 incident intelligence layer.

Naming and provenance follow the Day 2B precedent (docs/DAY2B_TRUTH_MODEL.md):

  - Every table here holds SYNTHETIC or SIMULATED data, never observed fact. The
    canonical `transactions` table is referenced read-only and is never written to.
  - `simulated_incident_outcomes` uses an explicit `simulated_*` column prefix so a
    modelled incident-period outcome can never be confused with `transactions.status`.
  - Machine-enforced provenance: `is_synthetic` / `is_simulated` /
    `is_evaluation_only` carry `NOT NULL DEFAULT true CHECK (... = true)`, so the
    database itself rejects any attempt to relabel this data as observed.

DOCUMENTED DEVIATION FROM docs/DAY3_IMPLEMENTATION_CONTRACT.md
--------------------------------------------------------------
The contract sketched `ground_truth_root_cause` as a column on `incidents`, guarded by
an `is_evaluation_only` flag. This implementation instead puts it in a SEPARATE table,
`incident_ground_truth`.

Reason: a flag on a shared row is a convention, and conventions leak. A separate table
makes the epistemic boundary structural -- the detection, evidence, hypothesis, and RCA
modules do not import this class and do not name this table, so ground truth cannot
reach a diagnosis path by an accidental `SELECT *`. Since ground-truth isolation is the
single invariant the whole project's credibility rests on, it is worth a table.

The contract's `incident_evaluation` concept is likewise split into
`incident_anomalies` (detection), `incident_hypotheses` (competing explanations), and
`incident_rca_results` (the conclusion), because Day 3 must persist a ranked hypothesis
set with both supporting and contradicting evidence -- which a single flat row cannot
represent honestly. All contract-required fields are preserved across the three tables.
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from aventum_ingest.models import Base

# Imported for its side effect: registering the Day 2B synthetic tables on the shared
# Base.metadata. Day 3 has foreign keys into `synthetic_gateways` and
# `synthetic_generation_runs`, which SQLAlchemy can only resolve once those tables are
# present in the same MetaData. Day 3 never writes to them.
from aventum_synth import models as _synth_models  # noqa: F401

from .constants import (
    EVIDENCE_TYPES,
    HYPOTHESIS_TYPES,
    INCIDENT_STATUSES,
    INCIDENT_TYPES,
    RCA_VERDICTS,
    RUN_STATUSES,
    SOURCE_LAYERS,
)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


class Incident(Base):
    """
    One injected, explicitly synthetic incident.

    An incident is a DEFINITION plus a lifecycle state. It holds no diagnosis and no
    ground truth: the modelled outcomes it implies live in `simulated_incident_outcomes`
    and its known-by-construction cause lives in `incident_ground_truth`.

    Idempotency: `incident_key` is a deterministic digest of the full definition
    (type, target, window, multipliers, seed, model version). Re-injecting the same
    definition resolves to the same row instead of creating a duplicate.
    """

    __tablename__ = "incidents"

    incident_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Deterministic identity of the DEFINITION -- the idempotency key.
    incident_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # Human-facing stable label, e.g. "golden-gateway-c-degradation".
    incident_name: Mapped[str] = mapped_column(Text, nullable=False)
    incident_type: Mapped[str] = mapped_column(Text, nullable=False)

    # --- target ---
    # Nullable: a systemic or issuer-centred incident has no single affected gateway.
    affected_gateway_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_incident_gateway"),
        nullable=True,
    )
    # Additional cohort narrowing, e.g. {"sender_bank": "SBI"}. NULL = whole gateway.
    affected_segment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- window (half-open [start, end), matching Day 2B health-window semantics) ---
    incident_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    incident_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- configured severity levers ---
    # These are multipliers on the Day 2B baseline profile, applied through
    # GatewayRuntimeProfile so failure, latency, and response mix move together.
    failure_multiplier: Mapped[object] = mapped_column(Numeric(8, 4), nullable=False)
    latency_multiplier: Mapped[object] = mapped_column(Numeric(8, 4), nullable=False)
    timeout_multiplier: Mapped[object] = mapped_column(Numeric(8, 4), nullable=False)
    # The intended degraded failure rate, recorded for auditability of the calibration.
    target_failure_rate: Mapped[object | None] = mapped_column(Numeric(8, 6), nullable=True)

    # --- lineage ---
    generation_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("synthetic_generation_runs.generation_run_id", name="fk_incident_generation_run"),
        nullable=False,
    )
    source_ingestion_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.ingestion_run_id", name="fk_incident_ingestion_run"),
        nullable=False,
    )

    # --- lifecycle ---
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="CREATED")

    incident_seed: Mapped[str] = mapped_column(Text, nullable=False)
    incident_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    incident_config_version: Mapped[str] = mapped_column(Text, nullable=False)

    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("incident_key", name="uq_incidents_key"),
        CheckConstraint("is_synthetic = true", name="ck_incident_is_synthetic"),
        CheckConstraint(f"status IN ({_sql_list(INCIDENT_STATUSES)})", name="ck_incident_status"),
        CheckConstraint(f"incident_type IN ({_sql_list(INCIDENT_TYPES)})", name="ck_incident_type"),
        # Half-open window must be non-degenerate. A zero-width window would make every
        # boundary test vacuous and every rate undefined.
        CheckConstraint("incident_end > incident_start", name="ck_incident_window"),
        CheckConstraint("failure_multiplier > 0", name="ck_incident_failure_multiplier"),
        CheckConstraint("latency_multiplier > 0", name="ck_incident_latency_multiplier"),
        CheckConstraint("timeout_multiplier > 0", name="ck_incident_timeout_multiplier"),
        Index("ix_incidents_window", "incident_start", "incident_end"),
        Index("ix_incidents_gateway", "affected_gateway_id"),
        Index("ix_incidents_status", "status"),
    )


class IncidentGroundTruth(Base):
    """
    EVALUATION ONLY. The known-by-construction cause of an injected incident.

    This table is deliberately isolated. Nothing in the detection, evidence, hypothesis,
    or RCA path reads it -- see the module docstring. It exists so Day 3's own accuracy
    can be scored AFTER a diagnosis has already been produced, and for no other purpose.

    Feeding this into diagnosis would make the evaluation circular and the whole
    RCA claim worthless.
    """

    __tablename__ = "incident_ground_truth"

    incident_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incidents.incident_id", name="fk_ground_truth_incident", ondelete="CASCADE"),
        primary_key=True,
    )
    ground_truth_root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    ground_truth_gateway_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    ground_truth_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    is_evaluation_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("is_evaluation_only = true", name="ck_ground_truth_evaluation_only"),
    )


class IncidentSimulationRun(Base):
    """
    One execution of the Approach B simulated-outcome generator.

    Mirrors `synthetic_generation_runs` so the simulated layer is auditable exactly the
    way the canonical load and the synthetic baseline are: which incident, which seed,
    which model version, how many rows, and what fingerprint the result hashed to.
    """

    __tablename__ = "incident_simulation_runs"

    simulation_run_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incidents.incident_id", name="fk_simulation_run_incident", ondelete="CASCADE"),
        nullable=False,
    )
    simulation_seed: Mapped[str] = mapped_column(Text, nullable=False)
    incident_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    incident_config_version: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="RUNNING")
    rows_in_window: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rows_simulated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rows_changed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # SHA-256 over the ordered, deterministically rendered simulated rows. Two runs with
    # identical inputs must produce an identical value; changing the seed or any
    # multiplier must change it.
    simulation_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    started_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("is_simulated = true", name="ck_simulation_run_is_simulated"),
        CheckConstraint(f"status IN ({_sql_list(RUN_STATUSES)})", name="ck_simulation_run_status"),
        CheckConstraint("rows_simulated >= 0", name="ck_simulation_run_rows"),
        CheckConstraint("rows_changed >= 0", name="ck_simulation_run_changed"),
        CheckConstraint(
            "rows_changed <= rows_simulated", name="ck_simulation_run_changed_bounded"
        ),
        Index("ix_simulation_runs_incident", "incident_id"),
    )


class SimulatedIncidentOutcome(Base):
    """
    The Approach B outcome layer -- Day 3's primary generative output.

    One row per transaction inside an incident window. NOT one row per canonical
    transaction: outside the window the effective outcome simply IS the observed
    outcome, and materialising 250,000 unchanged copies would add no information while
    inviting exactly the confusion this layer exists to prevent.

    `observed_status` is copied here for convenient side-by-side comparison. It is a
    READ-ONLY WITNESS of what history recorded -- it is never the value being
    "corrected", and `transactions.status` is never updated to match `simulated_status`.

    Coherence: `simulated_status`, `simulated_response_code`, `simulated_latency_regime`,
    and `simulated_latency_ms` are generated through one funnel
    (Day 2B's `GatewayRuntimeProfile` -> status -> response family -> regime -> value),
    so a degraded health state moves all four together. They are never mutated
    independently, and the CHECK constraints below make an incoherent row unstorable.
    """

    __tablename__ = "simulated_incident_outcomes"

    simulated_outcome_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    incident_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incidents.incident_id", name="fk_simulated_outcome_incident",
                   ondelete="CASCADE"),
        nullable=False,
    )
    simulation_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incident_simulation_runs.simulation_run_id",
                   name="fk_simulated_outcome_run", ondelete="CASCADE"),
        nullable=False,
    )

    # Read-only reference to observed data. CASCADE mirrors Day 2B: a re-ingestion that
    # removes a canonical row must unambiguously remove the modelled rows built on it
    # rather than silently orphan them.
    transaction_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("transactions.transaction_id", name="fk_simulated_outcome_transaction",
                   ondelete="CASCADE"),
        nullable=False,
    )

    # Which synthetic gateway carried it, denormalised from the Day 2B assignment so
    # cohort queries do not need a third join.
    gateway_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_simulated_outcome_gateway"),
        nullable=False,
    )

    # --- observed witness (never modified, never authoritative for the simulation) ---
    observed_status: Mapped[str] = mapped_column(Text, nullable=False)

    # --- modelled incident-period outcome ---
    simulated_status: Mapped[str] = mapped_column(Text, nullable=False)
    simulated_response_code: Mapped[str] = mapped_column(Text, nullable=False)
    simulated_response_attribution: Mapped[str] = mapped_column(Text, nullable=False)
    simulated_latency_regime: Mapped[str] = mapped_column(Text, nullable=False)
    simulated_latency_ms: Mapped[object] = mapped_column(Numeric(10, 2), nullable=False)
    # True when the model moved this transaction's outcome away from what was observed.
    outcome_changed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # The forward-model failure probability in force for this row, for auditability.
    modeled_failure_probability: Mapped[object] = mapped_column(Numeric(8, 6), nullable=False)
    # Whether this row was inside the incident's affected cohort at all. Rows in the
    # window but on a control gateway are simulated as unchanged, and saying so
    # explicitly is what makes control stability a queryable fact.
    in_affected_cohort: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # --- lineage ---
    source_ingestion_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ingestion_runs.ingestion_run_id", name="fk_simulated_outcome_ingestion_run"),
        nullable=False,
    )
    generation_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("synthetic_generation_runs.generation_run_id",
                   name="fk_simulated_outcome_generation_run"),
        nullable=False,
    )

    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "simulation_run_id", "transaction_id", name="uq_simulated_outcome_run_transaction"
        ),
        CheckConstraint("is_simulated = true", name="ck_simulated_outcome_is_simulated"),
        CheckConstraint(
            "observed_status IN ('SUCCESS', 'FAILED')", name="ck_simulated_outcome_observed_status"
        ),
        CheckConstraint(
            "simulated_status IN ('SUCCESS', 'FAILED')",
            name="ck_simulated_outcome_simulated_status",
        ),
        CheckConstraint("simulated_latency_ms > 0", name="ck_simulated_outcome_latency_positive"),
        # Coherence, enforced by the database so a generator bug cannot persist nonsense:
        # a modelled success is always APPROVED, and a modelled failure never is.
        CheckConstraint(
            "(simulated_status = 'SUCCESS') = (simulated_response_code = 'APPROVED')",
            name="ck_simulated_outcome_status_response_coherent",
        ),
        # A TIMEOUT response must carry TIMEOUT-regime latency and vice versa.
        CheckConstraint(
            "(simulated_response_code = 'TIMEOUT') = (simulated_latency_regime = 'TIMEOUT')",
            name="ck_simulated_outcome_timeout_coherent",
        ),
        # `outcome_changed` must actually describe the two status columns.
        CheckConstraint(
            "outcome_changed = (simulated_status <> observed_status)",
            name="ck_simulated_outcome_changed_coherent",
        ),
        # APPROACH B, ENFORCED AT THE DATABASE LEVEL. An observed failure may never be
        # simulated as a success: a degradation ADDS failures, it never moves or
        # rescues them. This single constraint is what makes the rejected Approach A
        # structurally unrepresentable rather than merely discouraged.
        CheckConstraint(
            "NOT (observed_status = 'FAILED' AND simulated_status = 'SUCCESS')",
            name="ck_simulated_outcome_approach_b_no_rescue",
        ),
        Index("ix_simulated_outcome_incident", "incident_id"),
        Index("ix_simulated_outcome_transaction", "transaction_id"),
        Index("ix_simulated_outcome_gateway", "incident_id", "gateway_id"),
    )


class IncidentAnalysisRun(Base):
    """
    One execution of the detect -> evidence -> hypothesis -> RCA chain.

    `incident_id` is NULLABLE on purpose: the detector must be runnable over a window
    with no injected incident at all, which is how the no-false-positive property is
    tested. An analysis run with a NULL incident is a legitimate baseline scan.
    """

    __tablename__ = "incident_analysis_runs"

    analysis_run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("incidents.incident_id", name="fk_analysis_run_incident", ondelete="CASCADE"),
        nullable=True,
    )

    analysis_window_start: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    analysis_window_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_window_start: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    baseline_window_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)

    analysis_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    detection_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="RUNNING")

    cohorts_scanned: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    anomalies_found: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # SHA-256 over the ordered analytical output (anomalies + evidence + ranking).
    analysis_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Runtime instrumentation, so the performance claims in the Day 3 report are
    # measured rather than asserted.
    detection_ms: Mapped[object | None] = mapped_column(Numeric(12, 3), nullable=True)
    evidence_ms: Mapped[object | None] = mapped_column(Numeric(12, 3), nullable=True)
    rca_ms: Mapped[object | None] = mapped_column(Numeric(12, 3), nullable=True)

    started_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("is_synthetic = true", name="ck_analysis_run_is_synthetic"),
        CheckConstraint(f"status IN ({_sql_list(RUN_STATUSES)})", name="ck_analysis_run_status"),
        CheckConstraint(
            "analysis_window_end > analysis_window_start", name="ck_analysis_run_window"
        ),
        CheckConstraint(
            "baseline_window_end > baseline_window_start", name="ck_analysis_run_baseline_window"
        ),
        Index("ix_analysis_runs_incident", "incident_id"),
    )


class IncidentAnomaly(Base):
    """
    One detected anomalous cohort.

    Produced by a deterministic two-proportion test, never by an LLM. The detector does
    not know which gateway (if any) is degraded -- it scans every cohort that clears the
    minimum-sample bar and ranks what it finds.
    """

    __tablename__ = "incident_anomalies"

    anomaly_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incident_analysis_runs.analysis_run_id", name="fk_anomaly_analysis_run",
                   ondelete="CASCADE"),
        nullable=False,
    )
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("incidents.incident_id", name="fk_anomaly_incident", ondelete="CASCADE"),
        nullable=True,
    )

    # --- what population this is about ---
    # e.g. "gateway=gateway_C" or "gateway=gateway_C|sender_bank=SBI". Stable and
    # human-readable, so an alert can be deduplicated and cited.
    cohort_key: Mapped[str] = mapped_column(Text, nullable=False)
    cohort_dimensions: Mapped[list] = mapped_column(JSONB, nullable=False)
    cohort_definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cohort_depth: Mapped[int] = mapped_column(Integer, nullable=False)

    detection_window_start: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    detection_window_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- measured comparison ---
    affected_population: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_population: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    current_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)

    baseline_failure_rate: Mapped[object] = mapped_column(Numeric(9, 6), nullable=False)
    current_failure_rate: Mapped[object] = mapped_column(Numeric(9, 6), nullable=False)
    absolute_delta: Mapped[object] = mapped_column(Numeric(9, 6), nullable=False)
    relative_delta: Mapped[object] = mapped_column(Numeric(12, 6), nullable=False)

    significance_sigma: Mapped[object] = mapped_column(Numeric(12, 4), nullable=False)
    anomaly_score: Mapped[object] = mapped_column(Numeric(12, 6), nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    # GMV computed from authoritative observed `transactions.amount`.
    gmv_total: Mapped[object] = mapped_column(Numeric(18, 2), nullable=False)
    gmv_at_risk: Mapped[object] = mapped_column(Numeric(18, 2), nullable=False)

    # True when a broader cohort already explains this one.
    suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    suppressed_by: Mapped[str | None] = mapped_column(Text, nullable=True)

    detected_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        UniqueConstraint("analysis_run_id", "cohort_key", name="uq_anomaly_run_cohort"),
        CheckConstraint("is_synthetic = true", name="ck_anomaly_is_synthetic"),
        CheckConstraint("affected_population >= 0", name="ck_anomaly_population"),
        CheckConstraint("cohort_depth >= 1", name="ck_anomaly_cohort_depth"),
        Index("ix_anomaly_run_rank", "analysis_run_id", "rank"),
        Index("ix_anomaly_incident", "incident_id"),
    )


class IncidentEvidence(Base):
    """
    One deterministic, quantified, traceable evidence record.

    Every value here is computed by a named SQL/analytical step recorded in
    `evidence_source`. No LLM may write a row in this table -- an RCA statement that
    cannot be traced back to an evidence_id is, for Aventum's purposes, not a finding.
    """

    __tablename__ = "incident_evidence"

    evidence_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incident_analysis_runs.analysis_run_id", name="fk_evidence_analysis_run",
                   ondelete="CASCADE"),
        nullable=False,
    )
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("incidents.incident_id", name="fk_evidence_incident", ondelete="CASCADE"),
        nullable=True,
    )
    anomaly_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("incident_anomalies.anomaly_id", name="fk_evidence_anomaly",
                   ondelete="CASCADE"),
        nullable=True,
    )

    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)

    cohort_key: Mapped[str] = mapped_column(Text, nullable=False)
    cohort_definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    gateway_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    segment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    baseline_value: Mapped[object | None] = mapped_column(Numeric(18, 6), nullable=True)
    current_value: Mapped[object | None] = mapped_column(Numeric(18, 6), nullable=True)
    delta: Mapped[object | None] = mapped_column(Numeric(18, 6), nullable=True)
    relative_delta: Mapped[object | None] = mapped_column(Numeric(18, 6), nullable=True)
    significance_sigma: Mapped[object | None] = mapped_column(Numeric(12, 4), nullable=True)

    # The control gateways' own value for the same metric over the same window --
    # the comparison that separates "this gateway broke" from "everything broke".
    control_group: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Which epistemic layer produced this number. Never flattened.
    source_layer: Mapped[str] = mapped_column(Text, nullable=False)
    # The named analytical step, for audit: e.g. "cohort_metrics:failure_rate".
    evidence_source: Mapped[str] = mapped_column(Text, nullable=False)

    time_window_start: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    time_window_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)

    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("is_synthetic = true", name="ck_evidence_is_synthetic"),
        CheckConstraint(f"evidence_type IN ({_sql_list(EVIDENCE_TYPES)})",
                        name="ck_evidence_type"),
        CheckConstraint(f"source_layer IN ({_sql_list(SOURCE_LAYERS)})",
                        name="ck_evidence_source_layer"),
        Index("ix_evidence_run", "analysis_run_id"),
        Index("ix_evidence_anomaly", "anomaly_id"),
        Index("ix_evidence_incident", "incident_id"),
    )


class IncidentHypothesis(Base):
    """
    One candidate explanation, scored with BOTH supporting and contradicting evidence.

    Storing contradiction is not decoration. A hypothesis engine that only records
    confirming evidence cannot express doubt, and a system that cannot express doubt
    will state a wrong cause with the same confidence as a right one.
    """

    __tablename__ = "incident_hypotheses"

    hypothesis_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incident_analysis_runs.analysis_run_id", name="fk_hypothesis_analysis_run",
                   ondelete="CASCADE"),
        nullable=False,
    )
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("incidents.incident_id", name="fk_hypothesis_incident", ondelete="CASCADE"),
        nullable=True,
    )

    hypothesis_type: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis_statement: Mapped[str] = mapped_column(Text, nullable=False)
    subject_dimension: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    score: Mapped[object] = mapped_column(Numeric(12, 6), nullable=False)
    confidence: Mapped[object] = mapped_column(Numeric(6, 4), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    supporting_evidence_ids: Mapped[list] = mapped_column(ARRAY(BigInteger), nullable=False)
    contradicting_evidence_ids: Mapped[list] = mapped_column(ARRAY(BigInteger), nullable=False)
    score_components: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        CheckConstraint("is_synthetic = true", name="ck_hypothesis_is_synthetic"),
        CheckConstraint(f"hypothesis_type IN ({_sql_list(HYPOTHESIS_TYPES)})",
                        name="ck_hypothesis_type"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_hypothesis_confidence"),
        CheckConstraint("rank >= 1", name="ck_hypothesis_rank"),
        Index("ix_hypothesis_run_rank", "analysis_run_id", "rank"),
    )


class IncidentRcaResult(Base):
    """
    The RCA conclusion for one analysis run.

    Kept strictly separate from `incident_ground_truth`. This row is what the system
    BELIEVES, derived only from evidence; that table is what is TRUE by construction.
    Comparing them is evaluation, and happens only after this row already exists.
    """

    __tablename__ = "incident_rca_results"

    rca_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incident_analysis_runs.analysis_run_id", name="fk_rca_analysis_run",
                   ondelete="CASCADE"),
        nullable=False,
    )
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("incidents.incident_id", name="fk_rca_incident", ondelete="CASCADE"),
        nullable=True,
    )

    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL when the verdict is INSUFFICIENT_EVIDENCE -- the system is required to be
    # able to decline, rather than always naming its best guess.
    predicted_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    predicted_hypothesis_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    predicted_gateway_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    predicted_segment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[object] = mapped_column(Numeric(6, 4), nullable=False)

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    affected_population: Mapped[dict] = mapped_column(JSONB, nullable=False)
    control_population: Mapped[dict] = mapped_column(JSONB, nullable=False)
    incident_window_start: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    incident_window_end: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)

    supporting_evidence_ids: Mapped[list] = mapped_column(ARRAY(BigInteger), nullable=False)
    contradicting_evidence_ids: Mapped[list] = mapped_column(ARRAY(BigInteger), nullable=False)
    alternatives_considered: Mapped[list] = mapped_column(JSONB, nullable=False)

    # SHA-256 over the ordered RCA content, for the reproducibility guarantee.
    rca_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        UniqueConstraint("analysis_run_id", name="uq_rca_analysis_run"),
        CheckConstraint("is_synthetic = true", name="ck_rca_is_synthetic"),
        CheckConstraint(f"verdict IN ({_sql_list(RCA_VERDICTS)})", name="ck_rca_verdict"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_rca_confidence"),
        # A named cause requires a verdict that permits one, and vice versa.
        CheckConstraint(
            "(verdict = 'INSUFFICIENT_EVIDENCE') = (predicted_root_cause IS NULL)",
            name="ck_rca_verdict_cause_coherent",
        ),
        Index("ix_rca_incident", "incident_id"),
    )
