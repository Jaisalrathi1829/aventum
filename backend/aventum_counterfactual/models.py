"""
SQLAlchemy models for the Day 4A simulation layer, plus the (unused in Day 4A) agent
run tables the schema contract defines.

Provenance follows the Day 2B/Day 3 precedent: `is_simulated` carries
`NOT NULL DEFAULT true CHECK (= true)`, so PostgreSQL itself refuses to let a
counterfactual projection be relabelled as observed fact.

WHY `agent_runs` / `agent_tool_calls` EXIST HERE WITH NO AGENT
--------------------------------------------------------------
Day 4A implements the deterministic spine and contains no Qwen, no Ollama client, no
tool registry, and no agent loop. The two tables are created anyway because
`counterfactual_simulations.agent_run_id` and `recommendations.agent_run_id` are
nullable FKs into them: defining the target now means Day 4B can attach an agent run
without a schema migration, and every Day 4A row simply leaves the FK NULL -- which is
precisely the contract's "produced deterministically, without narrative" case.
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

# Imported for their side effect: registering the Day 2B and Day 3 tables on the shared
# Base.metadata so this module's foreign keys into `synthetic_gateways`, `incidents`,
# and `incident_analysis_runs` resolve. Day 4 never writes to any of them.
from aventum_incident import models as _incident_models  # noqa: F401
from aventum_synth import models as _synth_models  # noqa: F401

from .constants import ACTION_TYPES, SIMULATION_STATUSES

# JSONB columns must map a Python None to SQL NULL, not to a JSON 'null' literal.
# Without none_as_null, `policy_reason_codes IS NOT NULL` evaluates TRUE for a JSON
# null, and the coherence CHECK would reject a legitimately PERMITTED recommendation --
# which is exactly how this was found: the constraint refused the first real run.
_Json = JSONB(none_as_null=True)

_AGENT_RUN_STATUSES = (
    "AGENT_UNAVAILABLE",
    "BUDGET_EXCEEDED",
    "FAILED",
    "RUNNING",
    "SUCCEEDED",
)
_TOOL_OUTCOMES = (
    "INSUFFICIENT_EVIDENCE",
    "INTERNAL_ERROR",
    "INVALID_REQUEST",
    "NO_DATA",
    "SAFETY_BLOCK",
    "SUCCESS",
    "TIMEOUT",
)


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


class AgentRun(Base):
    """One agent orchestration attempt. Unused in Day 4A -- reserved for Day 4B."""

    __tablename__ = "agent_runs"

    agent_run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    incident_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("incidents.incident_id", name="fk_agent_run_incident"), nullable=True
    )
    analysis_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("incident_analysis_runs.analysis_run_id", name="fk_agent_run_analysis"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Must record `think: false` when Day 4B populates it -- the measured runtime
    # requirement, not a style preference.
    model_options: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    turns_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tool_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    simulations_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    context_tokens_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(f"status IN ({_sql_list(_AGENT_RUN_STATUSES)})", name="ck_agent_run_status"),
        CheckConstraint("turns_used >= 0", name="ck_agent_run_turns"),
        CheckConstraint("tool_calls_used >= 0", name="ck_agent_run_tool_calls"),
        CheckConstraint("simulations_used >= 0", name="ck_agent_run_simulations"),
    )


class AgentToolCall(Base):
    """One tool invocation within an agent run. Unused in Day 4A -- reserved for Day 4B."""

    __tablename__ = "agent_tool_calls"

    tool_call_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agent_runs.agent_run_id", name="fk_tool_call_agent_run", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    request: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    response: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[object | None] = mapped_column(Numeric(12, 3), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("agent_run_id", "sequence", name="uq_tool_call_run_sequence"),
        CheckConstraint(f"outcome IN ({_sql_list(_TOOL_OUTCOMES)})", name="ck_tool_call_outcome"),
        CheckConstraint("sequence >= 0", name="ck_tool_call_sequence"),
        CheckConstraint("attempt >= 1", name="ck_tool_call_attempt"),
        Index("ix_toolcall_run", "agent_run_id", "sequence"),
    )


class CounterfactualSimulation(Base):
    """
    One evaluated candidate policy, including NO_ACTION.

    Every quantitative field a recommendation may cite lives on this row and nowhere
    else. That is deliberate: the recommendation builder reads its numbers from here by
    `simulation_id`, so a fabricated figure cannot enter the pipeline through a caller
    argument. Computing projections on the fly instead would make fabrication invisible.

    `capacity_utilization` is permanently NULL in Day 4 -- see constants.CAPACITY_UNAVAILABLE.
    """

    __tablename__ = "counterfactual_simulations"

    simulation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    incident_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("incidents.incident_id", name="fk_sim_incident"), nullable=False
    )
    analysis_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("incident_analysis_runs.analysis_run_id", name="fk_sim_analysis_run"),
        nullable=False,
    )
    # NULL for every Day 4A row: the deterministic spine runs without an agent.
    agent_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("agent_runs.agent_run_id", name="fk_sim_agent_run"), nullable=True
    )

    # Stable identity of the candidate, e.g. "NO_ACTION" or
    # "REROUTE:gateway_C->gateway_A@20.0". Part of the idempotency key.
    candidate_key: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_gateway_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_sim_source_gateway"),
        nullable=True,
    )
    target_gateway_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("synthetic_gateways.gateway_id", name="fk_sim_target_gateway"),
        nullable=True,
    )
    traffic_percentage: Mapped[object] = mapped_column(
        Numeric(5, 2), nullable=False, server_default="0"
    )

    status: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured machine-readable reason. Required exactly when status is invalid.
    invalid_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    affected_population: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rerouted_population: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    current_distribution: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    projected_distribution: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    baseline_success_rate: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    projected_success_rate: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    expected_success_delta: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    projected_failure_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # GMV amounts are OBSERVED (`transactions.amount`); which transactions succeed is
    # MODELLED. Never "recovered GMV" -- only projected, retained, or at risk.
    projected_gmv_total: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)
    projected_gmv_retained: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)
    projected_gmv_at_risk: Mapped[object | None] = mapped_column(Numeric(18, 2), nullable=True)

    projected_latency_p50: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    projected_latency_p95: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)
    latency_delta_ms: Mapped[object | None] = mapped_column(Numeric(12, 2), nullable=True)

    concentration_after: Mapped[object | None] = mapped_column(Numeric(6, 4), nullable=True)
    # Always NULL in Day 4. No capacity telemetry exists to populate it.
    capacity_utilization: Mapped[object | None] = mapped_column(Numeric(6, 4), nullable=True)
    eligibility_result: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    risk_score: Mapped[object | None] = mapped_column(Numeric(9, 6), nullable=True)
    risk_components: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    # The counterfactual's audit of itself: what it froze and what it moved.
    held_constant: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    changed_variables: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    assumptions: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    limitations: Mapped[dict | None] = mapped_column(_Json, nullable=True)

    simulation_seed: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 over every held-constant input. Re-derived at recommendation and execution
    # time; a mismatch means the world moved under the simulation.
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 over the ordered projected outcomes.
    simulation_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    profile_version: Mapped[str] = mapped_column(Text, nullable=False)
    elapsed_ms: Mapped[object | None] = mapped_column(Numeric(12, 3), nullable=True)

    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "incident_id", "candidate_key", "input_fingerprint", name="uq_simulation_identity"
        ),
        CheckConstraint("is_simulated = true", name="ck_sim_is_simulated"),
        CheckConstraint(f"status IN ({_sql_list(SIMULATION_STATUSES)})", name="ck_sim_status"),
        CheckConstraint(
            f"action_type IN ({_sql_list(ACTION_TYPES)})", name="ck_sim_action_type"
        ),
        CheckConstraint(
            "traffic_percentage >= 0 AND traffic_percentage <= 100", name="ck_sim_traffic_range"
        ),
        CheckConstraint(
            "(status = 'SIMULATION_INVALID') = (invalid_reason IS NOT NULL)",
            name="ck_sim_invalid_reason_coherent",
        ),
        CheckConstraint(
            "(action_type = 'NO_ACTION' AND target_gateway_id IS NULL "
            " AND traffic_percentage = 0) "
            "OR (action_type = 'REROUTE' AND target_gateway_id IS NOT NULL)",
            name="ck_sim_action_shape",
        ),
        CheckConstraint(
            "source_gateway_id IS NULL OR target_gateway_id IS NULL "
            "OR source_gateway_id <> target_gateway_id",
            name="ck_sim_source_differs_from_target",
        ),
        Index("ix_sim_incident", "incident_id", "candidate_key"),
        Index("ix_sim_fingerprint", "input_fingerprint"),
    )
